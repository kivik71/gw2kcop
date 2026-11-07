import pytest

from gw2kcop.accounts import AccountsError, load_accounts, parse_accounts

HEADER = "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
# Synthetic accounts (made-up owners and names).
GOOD = HEADER + (
    "ANN-BROKER-PLN,demo_broker,ann,,Ann broker PLN,brokerage,PLN,TRUE,parser\n"
    "ANN-BANK-EUR,demo_bank,ann,,Ann bank EUR,fx,EUR,TRUE,manual\n"
    "BOB-OLD-PLN,demo_bank,bob,,Bob closed,current,PLN,FALSE,manual\n"
)


def test_parses_good_file():
    accounts = parse_accounts(GOOD)
    assert [a.account_id for a in accounts] == ["ANN-BROKER-PLN", "ANN-BANK-EUR", "BOB-OLD-PLN"]
    assert accounts[0].active is True and accounts[2].active is False
    assert accounts[1].import_method == "manual"


@pytest.mark.parametrize(
    "row, message",
    [
        ("ann-broker,demo,ann,,x,brokerage,PLN,TRUE,parser", "invalid account_id"),
        ("../ETC,demo,ann,,x,brokerage,PLN,TRUE,parser", "invalid account_id"),
        ("ANN-X,demo,ann,,x,crypto,PLN,TRUE,parser", "unknown account_type"),
        ("ANN-X,demo,ann,,x,brokerage,pln,TRUE,parser", "currency"),
        ("ANN-X,demo,ann,,x,brokerage,PLN,yes,parser", "TRUE or FALSE"),
        ("ANN-X,demo,ann,,x,brokerage,PLN,TRUE,", "import_method"),
        ("ANN-X,Demo Bank,ann,,x,brokerage,PLN,TRUE,parser", "institution"),
    ],
)
def test_rejects_bad_rows(row, message):
    with pytest.raises(AccountsError, match=message):
        parse_accounts(HEADER + row + "\n")


def test_duplicate_id():
    row = "ANN-X,demo,ann,,x,brokerage,PLN,TRUE,parser\n"
    with pytest.raises(AccountsError, match="duplicate"):
        parse_accounts(HEADER + row + row)


def test_missing_column():
    with pytest.raises(AccountsError, match="import_method"):
        parse_accounts("account_id,institution,owner,export_identifier,name,account_type,currency,active\n")


def test_reports_all_errors_at_once():
    text = HEADER + "bad,demo,ann,,x,brokerage,PLN,TRUE,parser\nANN-X,demo,ann,,x,nope,PLN,TRUE,parser\n"
    with pytest.raises(AccountsError) as exc:
        parse_accounts(text)
    assert "invalid account_id" in str(exc.value) and "unknown account_type" in str(exc.value)


def test_load_from_file_with_bom(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text(GOOD, encoding="utf-8-sig")
    assert len(load_accounts(path)) == 3


def test_load_missing_file(tmp_path):
    with pytest.raises(AccountsError, match="not found"):
        load_accounts(tmp_path / "accounts.csv")
