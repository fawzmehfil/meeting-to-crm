from __future__ import annotations

from meeting_to_crm.candidates import CandidateResolver


def _ids(items) -> set[str]:
    return {item.id for item in items}


def test_mixed_apex_contexts_are_preserved(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    context = CandidateResolver(client, config_factory()).resolve(load_meeting("m07.json"))

    assert _ids(context.companies) == {"company_1", "company_2"}
    assert _ids(context.deals) == {"deal_1", "deal_2"}


def test_multiple_cobalt_deals_are_preserved(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    context = CandidateResolver(client, config_factory()).resolve(load_meeting("m08.json"))

    assert _ids(context.companies) == {"company_4"}
    assert _ids(context.deals) == {"deal_5", "deal_6"}


def test_personal_email_name_collision_is_not_collapsed(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    context = CandidateResolver(client, config_factory()).resolve(load_meeting("m11.json"))

    assert _ids(context.contacts) == {"contact_10", "contact_11"}
    assert _ids(context.companies) == {"company_8", "company_9"}
    assert _ids(context.deals) == {"deal_9", "deal_10"}


def test_contractor_does_not_hide_primary_customer(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    context = CandidateResolver(client, config_factory()).resolve(load_meeting("m12.json"))

    assert _ids(context.contacts) == {"contact_1", "contact_12"}
    assert _ids(context.companies) == {"company_1", "company_10"}
    assert _ids(context.deals) == {"deal_1"}


def test_unresolved_personal_email_produces_no_candidates(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    context = CandidateResolver(client, config_factory()).resolve(load_meeting("m10.json"))

    assert not context.contacts
    assert not context.companies
    assert not context.deals
