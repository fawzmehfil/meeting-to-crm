from __future__ import annotations

from typing import Any

from ergo_crm import ErgoCRMClient
from meeting_to_crm.config import Config
from meeting_to_crm.models import CandidateContext, CandidateEntity, EntityRef, MeetingWebhook

_PUBLIC_FIELDS = {
    "company": {"id", "name", "domain", "industry", "employee_band"},
    "contact": {
        "id",
        "first_name",
        "last_name",
        "email",
        "title",
        "role_in_deal",
    },
    "deal": {
        "id",
        "name",
        "status",
        "pipeline",
        "stage",
        "amount",
        "next_step",
        "next_step_date",
        "competitor",
        "champion",
        "risk",
    },
}


def email_domain(email: str) -> str:
    _, separator, domain = email.strip().casefold().rpartition("@")
    return domain if separator else ""


def all_attendees_internal(meeting: MeetingWebhook, domains: frozenset[str]) -> bool:
    return bool(meeting.attendees) and all(
        email_domain(attendee.email) in domains for attendee in meeting.attendees
    )


class _CandidateBuilder:
    def __init__(self) -> None:
        self.entities: dict[str, dict[str, CandidateEntity]] = {
            "company": {},
            "contact": {},
            "deal": {},
        }

    def add(self, entity_type: str, record: dict[str, Any], reason: str) -> CandidateEntity:
        entity_id = str(record["id"])
        existing = self.entities[entity_type].get(entity_id)
        if existing is None:
            clean = {
                key: value for key, value in record.items() if key in _PUBLIC_FIELDS[entity_type]
            }
            existing = CandidateEntity(
                type=entity_type,  # type: ignore[arg-type]
                id=entity_id,
                record=clean,
                discovered_by=[reason],
            )
            self.entities[entity_type][entity_id] = existing
        elif reason not in existing.discovered_by:
            existing.discovered_by.append(reason)
        return existing

    def link(self, left_type: str, left_id: str, right_type: str, right_id: str) -> None:
        left = self.entities[left_type].get(left_id)
        right = self.entities[right_type].get(right_id)
        if left is None or right is None:
            return
        right_ref = EntityRef(type=right_type, id=right_id)  # type: ignore[arg-type]
        left_ref = EntityRef(type=left_type, id=left_id)  # type: ignore[arg-type]
        if right_ref not in left.associations:
            left.associations.append(right_ref)
        if left_ref not in right.associations:
            right.associations.append(left_ref)

    def context(self) -> CandidateContext:
        def ordered(entity_type: str) -> list[CandidateEntity]:
            values = self.entities[entity_type].values()
            for item in values:
                item.discovered_by.sort()
                item.associations.sort(key=lambda ref: (ref.type, ref.id))
            return sorted(values, key=lambda item: item.id)

        return CandidateContext(
            companies=ordered("company"),
            contacts=ordered("contact"),
            deals=ordered("deal"),
        )


class CandidateResolver:
    def __init__(self, client: ErgoCRMClient, config: Config) -> None:
        self.client = client
        self.config = config

    def resolve(self, meeting: MeetingWebhook) -> CandidateContext:
        builder = _CandidateBuilder()
        expanded_contacts: set[str] = set()
        expanded_companies: set[str] = set()

        for attendee in meeting.attendees:
            domain = email_domain(attendee.email)
            if not domain or domain in self.config.internal_domains:
                continue

            if domain in self.config.personal_domains:
                matches = self.client.search("contact", attendee.name)
                for record in matches:
                    contact = builder.add(
                        "contact",
                        record,
                        f"personal-email name search: {attendee.name}",
                    )
                    self._expand_contact(
                        builder,
                        contact.id,
                        expanded_contacts,
                        expanded_companies,
                    )
                continue

            contact_record = self.client.find_by_field(
                "contact", "email", attendee.email.strip().casefold()
            )
            if contact_record:
                contact = builder.add(
                    "contact",
                    contact_record,
                    f"exact contact email: {attendee.email.strip().casefold()}",
                )
                self._expand_contact(
                    builder,
                    contact.id,
                    expanded_contacts,
                    expanded_companies,
                )

            company_record = self.client.find_by_field("company", "domain", domain)
            if company_record:
                company = builder.add("company", company_record, f"exact company domain: {domain}")
                self._expand_company(builder, company.id, expanded_companies)

        return builder.context()

    def _expand_contact(
        self,
        builder: _CandidateBuilder,
        contact_id: str,
        expanded_contacts: set[str],
        expanded_companies: set[str],
    ) -> None:
        if contact_id in expanded_contacts:
            return
        expanded_contacts.add(contact_id)

        for company_record in self.client.list_associated("contact", contact_id, "contact_company"):
            company = builder.add(
                "company", company_record, f"associated with contact {contact_id}"
            )
            builder.link("contact", contact_id, "company", company.id)
            self._expand_company(builder, company.id, expanded_companies)

        for deal_record in self.client.list_associated("contact", contact_id, "deal_contact"):
            deal = builder.add("deal", deal_record, f"associated with contact {contact_id}")
            builder.link("contact", contact_id, "deal", deal.id)

    def _expand_company(
        self,
        builder: _CandidateBuilder,
        company_id: str,
        expanded_companies: set[str],
    ) -> None:
        if company_id in expanded_companies:
            return
        expanded_companies.add(company_id)
        for deal_record in self.client.list_associated("company", company_id, "deal_company"):
            deal = builder.add("deal", deal_record, f"associated with company {company_id}")
            builder.link("company", company_id, "deal", deal.id)
