# Polish formats

One folder per institution, one TOML file per export format, for example
`mbank_brokerage/transactions.toml`. The TOML schema (encoding, delimiter, header row,
skip rules, column mapping, type rules) is defined in stage 1.5 together with the first
parser. Complex formats (XTB `.xlsx`, PPK NN `.xls`) are Python modules in this folder.
