"""Fiscal years and accounting periods (spec/api.md §5.7, domain §9.1–9.2)."""

import datetime

from django.db import IntegrityError, transaction

from ..errors import NotFound, Unprocessable
from ..models import AccountingPeriod, FiscalYear
from ..values import fmt_date, parse_date


def fiscal_year_json(y):
    return {"id": y.id, "name": y.name, "start_date": fmt_date(y.start_date), "end_date": fmt_date(y.end_date), "status": y.status}


def period_json(p):
    return {
        "id": p.id, "fiscal_year_id": p.fiscal_year_id, "name": p.name,
        "start_date": fmt_date(p.start_date), "end_date": fmt_date(p.end_date), "status": p.status,
    }


def _dates(b):
    start = parse_date(b.required_str("start_date"), "start_date")
    end = parse_date(b.required_str("end_date"), "end_date")
    if end < start:
        raise Unprocessable("end_date must not be before start_date")
    return start, end


def list_fiscal_years():
    return [fiscal_year_json(y) for y in FiscalYear.objects.order_by("start_date", "id")]


def get_fiscal_year(id):
    y = FiscalYear.objects.filter(pk=id).first()
    if y is None:
        raise NotFound("fiscal year not found")
    return y


def create_fiscal_year(b):
    name = b.required_str("name")
    b.required_str("start_date")
    b.required_str("end_date")
    start, end = _dates(b)
    return FiscalYear.objects.create(name=name, start_date=start, end_date=end).id


def update_fiscal_year(id, b):
    name = b.required_str("name")
    b.required_str("start_date")
    b.required_str("end_date")
    get_fiscal_year(id)
    start, end = _dates(b)
    FiscalYear.objects.filter(pk=id).update(name=name, start_date=start, end_date=end)


def list_periods():
    return [period_json(p) for p in AccountingPeriod.objects.order_by("start_date", "id")]


def get_period(id):
    p = AccountingPeriod.objects.filter(pk=id).first()
    if p is None:
        raise NotFound("accounting period not found")
    return p


def _period_required(b):
    b.required_id("fiscal_year_id")
    b.required_str("name")
    b.required_str("start_date")
    b.required_str("end_date")


def create_period(b):
    _period_required(b)
    start, end = _dates(b)
    year = FiscalYear.objects.filter(pk=b.int("fiscal_year_id")).first()
    if year is None:
        raise Unprocessable("unknown fiscal_year_id")
    if year.status != "open":
        raise Unprocessable("the fiscal year is closed")
    return AccountingPeriod.objects.create(fiscal_year=year, name=b.str("name"), start_date=start, end_date=end).id


def update_period(id, b):
    _period_required(b)
    get_period(id)
    start, end = _dates(b)
    status = b.text("status") or "open"
    if status not in ("open", "closed"):
        raise Unprocessable("status must be open or closed")
    AccountingPeriod.objects.filter(pk=id).update(
        fiscal_year_id=b.int("fiscal_year_id"), name=b.str("name"), start_date=start, end_date=end, status=status
    )


def next_period_proposal():
    """The month after the latest period, for the new-period form (domain §13 A1)."""
    last = AccountingPeriod.objects.order_by("-end_date").first()
    if last is None:
        return None
    start = last.end_date + datetime.timedelta(days=1)
    end = _month_end(start)
    year = FiscalYear.objects.filter(start_date__lte=start, end_date__gte=start).first()
    if year is not None:
        end = min(end, year.end_date)
    return {"fiscal_year_id": year.id if year else None, "name": start.strftime("%Y-%m"), "start_date": start, "end_date": end}


def _month_end(d):
    first_next = (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return first_next - datetime.timedelta(days=1)


def period_for_posting(date):
    """The open period covering `date`, creating a monthly one if needed (domain §9.2)."""
    covering = AccountingPeriod.objects.filter(start_date__lte=date, end_date__gte=date).first()
    if covering is not None:
        if covering.status != "open":
            raise Unprocessable(f"the accounting period covering {date} is closed")
        return covering
    year = FiscalYear.objects.filter(start_date__lte=date, end_date__gte=date, status="open").first()
    if year is None:
        raise Unprocessable(f"no open accounting period or fiscal year covers {date}")
    start = max(date.replace(day=1), year.start_date)
    end = min(_month_end(date), year.end_date)
    try:
        with transaction.atomic():
            return AccountingPeriod.objects.create(fiscal_year=year, name=date.strftime("%Y-%m"), start_date=start, end_date=end)
    except IntegrityError:
        raise Unprocessable(f"cannot create a period for {date}: it would overlap an existing period")
