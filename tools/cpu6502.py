#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A small, complete NMOS 6502 interpreter (documented opcodes only).

Enough to execute real NES code against a Bus object exposing read(a)/write(a,v).
No decimal mode: the 2A03 does not have it. Cycles are approximate - this is
for behaviour, not timing.
"""

IMP, ACC, IMM, ZP, ZPX, ZPY, ABS, ABX, ABY, IND, IZX, IZY, REL = range(13)

OPS = {}
def _t(spec):
    tok = spec.split()
    for i in range(0, len(tok), 3):
        op, mn, am = tok[i:i + 3]
        OPS[int(op, 16)] = (mn, globals()[am])

_t("""
00 BRK IMP   01 ORA IZX   05 ORA ZP    06 ASL ZP    08 PHP IMP   09 ORA IMM
0A ASL ACC   0D ORA ABS   0E ASL ABS   10 BPL REL   11 ORA IZY   15 ORA ZPX
16 ASL ZPX   18 CLC IMP   19 ORA ABY   1D ORA ABX   1E ASL ABX   20 JSR ABS
21 AND IZX   24 BIT ZP    25 AND ZP    26 ROL ZP    28 PLP IMP   29 AND IMM
2A ROL ACC   2C BIT ABS   2D AND ABS   2E ROL ABS   30 BMI REL   31 AND IZY
35 AND ZPX   36 ROL ZPX   38 SEC IMP   39 AND ABY   3D AND ABX   3E ROL ABX
40 RTI IMP   41 EOR IZX   45 EOR ZP    46 LSR ZP    48 PHA IMP   49 EOR IMM
4A LSR ACC   4C JMP ABS   4D EOR ABS   4E LSR ABS   50 BVC REL   51 EOR IZY
55 EOR ZPX   56 LSR ZPX   58 CLI IMP   59 EOR ABY   5D EOR ABX   5E LSR ABX
60 RTS IMP   61 ADC IZX   65 ADC ZP    66 ROR ZP    68 PLA IMP   69 ADC IMM
6A ROR ACC   6C JMP IND   6D ADC ABS   6E ROR ABS   70 BVS REL   71 ADC IZY
75 ADC ZPX   76 ROR ZPX   78 SEI IMP   79 ADC ABY   7D ADC ABX   7E ROR ABX
81 STA IZX   84 STY ZP    85 STA ZP    86 STX ZP    88 DEY IMP   8A TXA IMP
8C STY ABS   8D STA ABS   8E STX ABS   90 BCC REL   91 STA IZY   94 STY ZPX
95 STA ZPX   96 STX ZPY   98 TYA IMP   99 STA ABY   9A TXS IMP   9D STA ABX
A0 LDY IMM   A1 LDA IZX   A2 LDX IMM   A4 LDY ZP    A5 LDA ZP    A6 LDX ZP
A8 TAY IMP   A9 LDA IMM   AA TAX IMP   AC LDY ABS   AD LDA ABS   AE LDX ABS
B0 BCS REL   B1 LDA IZY   B4 LDY ZPX   B5 LDA ZPX   B6 LDX ZPY   B8 CLV IMP
B9 LDA ABY   BA TSX IMP   BC LDY ABX   BD LDA ABX   BE LDX ABY   C0 CPY IMM
C1 CMP IZX   C4 CPY ZP    C5 CMP ZP    C6 DEC ZP    C8 INY IMP   C9 CMP IMM
CA DEX IMP   CC CPY ABS   CD CMP ABS   CE DEC ABS   D0 BNE REL   D1 CMP IZY
D5 CMP ZPX   D6 DEC ZPX   D8 CLD IMP   D9 CMP ABY   DD CMP ABX   DE DEC ABX
E0 CPX IMM   E1 SBC IZX   E4 CPX ZP    E5 SBC ZP    E6 INC ZP    E8 INX IMP
E9 SBC IMM   EA NOP IMP   EC CPX ABS   ED SBC ABS   EE INC ABS   F0 BEQ REL
F1 SBC IZY   F5 SBC ZPX   F6 INC ZPX   F8 SED IMP   F9 SBC ABY   FD SBC ABX
FE INC ABX
""")


class IllegalOpcode(Exception):
    pass


class CPU:
    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.s = 0xFD
        self.pc = 0
        self.c = self.z = self.i = self.d = self.v = self.n = False
        self.cycles = 0

    # ---- flag helpers ----
    def _nz(self, v):
        v &= 0xFF
        self.z = (v == 0)
        self.n = bool(v & 0x80)
        return v

    def _p(self, brk=False):
        return ((0x80 if self.n else 0) | (0x40 if self.v else 0) | 0x20 |
                (0x10 if brk else 0) | (0x08 if self.d else 0) |
                (0x04 if self.i else 0) | (0x02 if self.z else 0) |
                (0x01 if self.c else 0))

    def _setp(self, v):
        self.n = bool(v & 0x80); self.v = bool(v & 0x40)
        self.d = bool(v & 0x08); self.i = bool(v & 0x04)
        self.z = bool(v & 0x02); self.c = bool(v & 0x01)

    # ---- stack ----
    def push(self, v):
        self.bus.write(0x100 | self.s, v & 0xFF)
        self.s = (self.s - 1) & 0xFF

    def pop(self):
        self.s = (self.s + 1) & 0xFF
        return self.bus.read(0x100 | self.s)

    # ---- addressing ----
    def _operand(self, am):
        """Return (address, is_immediate_value). Address is None for IMP/ACC."""
        b, pc = self.bus, self.pc
        if am in (IMP, ACC):
            return None
        if am == IMM:
            self.pc += 1
            return pc
        if am == ZP:
            self.pc += 1
            return b.read(pc)
        if am == ZPX:
            self.pc += 1
            return (b.read(pc) + self.x) & 0xFF
        if am == ZPY:
            self.pc += 1
            return (b.read(pc) + self.y) & 0xFF
        if am == ABS:
            self.pc += 2
            return b.read(pc) | (b.read(pc + 1) << 8)
        if am == ABX:
            self.pc += 2
            return ((b.read(pc) | (b.read(pc + 1) << 8)) + self.x) & 0xFFFF
        if am == ABY:
            self.pc += 2
            return ((b.read(pc) | (b.read(pc + 1) << 8)) + self.y) & 0xFFFF
        if am == IND:                      # with the NMOS page-wrap bug
            self.pc += 2
            p = b.read(pc) | (b.read(pc + 1) << 8)
            lo = b.read(p)
            hi = b.read((p & 0xFF00) | ((p + 1) & 0xFF))
            return lo | (hi << 8)
        if am == IZX:
            self.pc += 1
            z = (b.read(pc) + self.x) & 0xFF
            return b.read(z) | (b.read((z + 1) & 0xFF) << 8)
        if am == IZY:
            self.pc += 1
            z = b.read(pc)
            base = b.read(z) | (b.read((z + 1) & 0xFF) << 8)
            return (base + self.y) & 0xFFFF
        if am == REL:
            self.pc += 1
            off = b.read(pc)
            return (self.pc + ((off ^ 0x80) - 0x80)) & 0xFFFF
        raise AssertionError(am)

    # ---- execute one instruction ----
    def step(self):
        op = self.bus.read(self.pc)
        ent = OPS.get(op)
        if ent is None:
            raise IllegalOpcode("$%02X at $%04X" % (op, self.pc))
        mn, am = ent
        self.pc = (self.pc + 1) & 0xFFFF
        ea = self._operand(am)
        self.cycles += 4
        b = self.bus

        # loads / stores
        if   mn == "LDA": self.a = self._nz(b.read(ea))
        elif mn == "LDX": self.x = self._nz(b.read(ea))
        elif mn == "LDY": self.y = self._nz(b.read(ea))
        elif mn == "STA": b.write(ea, self.a)
        elif mn == "STX": b.write(ea, self.x)
        elif mn == "STY": b.write(ea, self.y)
        # transfers
        elif mn == "TAX": self.x = self._nz(self.a)
        elif mn == "TAY": self.y = self._nz(self.a)
        elif mn == "TXA": self.a = self._nz(self.x)
        elif mn == "TYA": self.a = self._nz(self.y)
        elif mn == "TSX": self.x = self._nz(self.s)
        elif mn == "TXS": self.s = self.x
        # stack
        elif mn == "PHA": self.push(self.a)
        elif mn == "PLA": self.a = self._nz(self.pop())
        elif mn == "PHP": self.push(self._p(True))
        elif mn == "PLP": self._setp(self.pop())
        # logic
        elif mn == "AND": self.a = self._nz(self.a & b.read(ea))
        elif mn == "ORA": self.a = self._nz(self.a | b.read(ea))
        elif mn == "EOR": self.a = self._nz(self.a ^ b.read(ea))
        elif mn == "BIT":
            m = b.read(ea)
            self.z = (self.a & m) == 0
            self.n = bool(m & 0x80); self.v = bool(m & 0x40)
        # arithmetic
        elif mn == "ADC":
            m = b.read(ea); t = self.a + m + (1 if self.c else 0)
            self.v = bool(~(self.a ^ m) & (self.a ^ t) & 0x80)
            self.c = t > 0xFF
            self.a = self._nz(t)
        elif mn == "SBC":
            m = b.read(ea) ^ 0xFF; t = self.a + m + (1 if self.c else 0)
            self.v = bool(~(self.a ^ m) & (self.a ^ t) & 0x80)
            self.c = t > 0xFF
            self.a = self._nz(t)
        elif mn in ("CMP", "CPX", "CPY"):
            r = {"CMP": self.a, "CPX": self.x, "CPY": self.y}[mn]
            m = b.read(ea)
            self.c = r >= m
            self._nz((r - m) & 0xFF)
        # inc / dec
        elif mn == "INC": b.write(ea, self._nz(b.read(ea) + 1))
        elif mn == "DEC": b.write(ea, self._nz(b.read(ea) - 1))
        elif mn == "INX": self.x = self._nz(self.x + 1)
        elif mn == "DEX": self.x = self._nz(self.x - 1)
        elif mn == "INY": self.y = self._nz(self.y + 1)
        elif mn == "DEY": self.y = self._nz(self.y - 1)
        # shifts
        elif mn in ("ASL", "LSR", "ROL", "ROR"):
            v = self.a if am == ACC else b.read(ea)
            if mn == "ASL":
                self.c = bool(v & 0x80); v = (v << 1) & 0xFF
            elif mn == "LSR":
                self.c = bool(v & 0x01); v >>= 1
            elif mn == "ROL":
                nc = bool(v & 0x80); v = ((v << 1) | (1 if self.c else 0)) & 0xFF; self.c = nc
            else:
                nc = bool(v & 0x01); v = (v >> 1) | (0x80 if self.c else 0); self.c = nc
            v = self._nz(v)
            if am == ACC: self.a = v
            else: b.write(ea, v)
        # branches
        elif mn in ("BPL","BMI","BVC","BVS","BCC","BCS","BNE","BEQ"):
            take = {"BPL": not self.n, "BMI": self.n, "BVC": not self.v,
                    "BVS": self.v, "BCC": not self.c, "BCS": self.c,
                    "BNE": not self.z, "BEQ": self.z}[mn]
            if take: self.pc = ea
        # jumps
        elif mn == "JMP": self.pc = ea
        elif mn == "JSR":
            r = (self.pc - 1) & 0xFFFF
            self.push(r >> 8); self.push(r & 0xFF)
            self.pc = ea
        elif mn == "RTS":
            lo = self.pop(); hi = self.pop()
            self.pc = ((hi << 8) | lo) + 1 & 0xFFFF
        elif mn == "RTI":
            self._setp(self.pop())
            lo = self.pop(); hi = self.pop()
            self.pc = (hi << 8) | lo
        elif mn == "BRK":
            r = (self.pc + 1) & 0xFFFF
            self.push(r >> 8); self.push(r & 0xFF); self.push(self._p(True))
            self.i = True
            self.pc = b.read(0xFFFE) | (b.read(0xFFFF) << 8)
        # flags
        elif mn == "CLC": self.c = False
        elif mn == "SEC": self.c = True
        elif mn == "CLI": self.i = False
        elif mn == "SEI": self.i = True
        elif mn == "CLV": self.v = False
        elif mn == "CLD": self.d = False
        elif mn == "SED": self.d = True
        elif mn == "NOP": pass
        else:
            raise IllegalOpcode("unhandled %s" % mn)
