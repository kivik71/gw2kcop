"""Parsers, grouped by country.

Config-first: simple CSV exports are described by one TOML file each in
``parsers/<country>/<institution>/<export>.toml`` and read by a generic engine.
Only complex formats (XTB .xlsx, PPK NN .xls) get Python code.
"""
