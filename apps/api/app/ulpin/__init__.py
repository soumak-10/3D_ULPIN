"""The 3D ULPIN itself.

Composition, parsing, the storey and unit codes, and the ISO 7064 MOD 36,36
check character. Pure functions over strings with no I/O and no framework
imports, because the identical algorithm also runs in PL/pgSQL and TypeScript
and the three must agree byte for byte.
"""
