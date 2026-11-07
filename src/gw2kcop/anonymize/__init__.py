"""Format-preserving anonymizer for export samples (stage 1.4).

Turns a real export into a sample that can be shared for parser development while
keeping the file structure byte-for-byte compatible (encoding, BOM, delimiter,
quoting, line endings, number and date formats).

Part 1 handles CSV; XLSX/XLS follow in part 2.
"""
