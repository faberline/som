"""The CSV behavior oracle accepts equivalent values and rejects lost data."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_business_workflows import check_csv_values

FIELDS = [{"name": name, "type": kind} for name, kind in
          [("active", "bool"), ("count", "int"), ("amount", "float"),
           ("day", "date"), ("note", "str"), ("optional", "str")]]
ROWS = [{"active": True, "count": 0, "amount": 1.0,
         "day": "2030-01-02", "note": " exact ", "optional": None}]
VALID = "active,count,amount,day,note,optional\ntrue,0,1.00,2030-01-02, exact ,\\N\n"


def test_csv_compares_declared_values():
    check_csv_values(VALID, FIELDS, ROWS)
    check_csv_values(VALID.replace("true", "True"), FIELDS, ROWS)


@pytest.mark.parametrize("invalid", [
    VALID.replace("true", "false"),
    VALID.replace("true", "yes"),
    VALID.replace("1.00", "2.00"),
    VALID.replace("1.00", "NaN"),
    VALID.replace(" exact ", "exact"),
    VALID.replace("\\N", ""),
    VALID.replace("active,count", "count,active"),
    VALID + VALID.splitlines()[1] + "\n",
])
def test_csv_rejects_changed_data(invalid):
    with pytest.raises((AssertionError, ValueError)):
        check_csv_values(invalid, FIELDS, ROWS)
