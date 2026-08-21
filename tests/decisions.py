from __future__ import annotations

from meeting_to_crm.models import (
    Certainty,
    DealDisposition,
    DecisionOutcome,
    Evidence,
    EvidenceSource,
    MeetingClassification,
    MeetingDecision,
    ProposedFieldChange,
)


def _decision(
    *,
    classification: MeetingClassification = MeetingClassification.customer_sales,
    outcome: DecisionOutcome = DecisionOutcome.update_existing,
    company_id: str | None,
    contact_ids: list[str],
    deal_id: str | None,
    disposition: DealDisposition = DealDisposition.existing,
    evidence: list[Evidence],
    changes: list[ProposedFieldChange] | None = None,
    reason_codes: list[str] | None = None,
    ambiguities: list[str] | None = None,
) -> MeetingDecision:
    return MeetingDecision(
        classification=classification,
        outcome=outcome,
        company_id=company_id,
        contact_ids=contact_ids,
        deal_id=deal_id,
        deal_disposition=disposition,
        certainty=Certainty.high,
        proposed_changes=changes or [],
        evidence=evidence,
        reason_codes=reason_codes or ["EXPLICIT_EXISTING_DEAL"],
        ambiguities=ambiguities or [],
    )


def _selection_evidence(
    quote: str,
    *,
    company_id: str,
    contact_ids: list[str],
    deal_id: str,
) -> list[Evidence]:
    supports = [
        "classification",
        f"company:{company_id}",
        *(f"contact:{contact_id}" for contact_id in contact_ids),
        f"deal:{deal_id}",
        "deal_disposition",
    ]
    return [
        Evidence(source=EvidenceSource.notes, quote=quote, supports=target) for target in supports
    ]


def fixture_decisions() -> dict[str, MeetingDecision]:
    m01_evidence = [
        Evidence(
            source=EvidenceSource.notes,
            quote="They want a deeper product demo this Thursday.",
            supports="field:deal.next_step",
        ),
        Evidence(
            source=EvidenceSource.notes,
            quote="They want a deeper product demo this Thursday.",
            supports="field:deal.next_step_date",
        ),
        Evidence(
            source=EvidenceSource.notes,
            quote="Clari is also in the evaluation.",
            supports="field:deal.competitor",
        ),
    ]
    m15_evidence = [
        Evidence(
            source=EvidenceSource.transcript,
            quote="Thursday September 3 is the next step.",
            supports="field:deal.next_step",
        ),
        Evidence(
            source=EvidenceSource.transcript,
            quote="Thursday September 3 is the next step.",
            supports="field:deal.next_step_date",
        ),
        Evidence(
            source=EvidenceSource.transcript,
            quote="I am the one who will walk the recommendation through.",
            supports="field:deal.champion",
        ),
    ]
    return {
        "mtg_8c2e41a7b9f04d6e": _decision(
            company_id="company_1",
            contact_ids=["contact_1", "contact_2"],
            deal_id="deal_1",
            evidence=_selection_evidence(
                "Follow-up on Apex Enterprise. Nina and Chris walked through remaining eval "
                "questions.",
                company_id="company_1",
                contact_ids=["contact_1", "contact_2"],
                deal_id="deal_1",
            ),
            changes=[
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_1",
                    field="next_step",
                    value="Deeper product demo",
                    evidence=[m01_evidence[0]],
                ),
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_1",
                    field="next_step_date",
                    value="2026-08-13",
                    evidence=[m01_evidence[1]],
                ),
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_1",
                    field="competitor",
                    value="clari",
                    evidence=[m01_evidence[2]],
                ),
            ],
        ),
        "mtg_1a9f3c8e2d7b5a01": _decision(
            outcome=DecisionOutcome.review,
            company_id=None,
            contact_ids=[],
            deal_id=None,
            disposition=DealDisposition.new,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="First conversation with Harborview Labs.",
                    supports="classification",
                )
            ],
            reason_codes=["NET_NEW_COMPANY_REQUIRES_REVIEW"],
        ),
        "mtg_4e6b0c1d8a2f9375": _decision(
            outcome=DecisionOutcome.review,
            company_id="company_5",
            contact_ids=["contact_7"],
            deal_id=None,
            disposition=DealDisposition.new,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="this would be Driftwood's first deal conversation.",
                    supports="deal",
                )
            ],
            reason_codes=["NET_NEW_DEAL_REQUIRES_REVIEW"],
        ),
        "mtg_b7d21e90c4a65f18": _decision(
            classification=MeetingClassification.internal,
            outcome=DecisionOutcome.skip,
            company_id=None,
            contact_ids=[],
            deal_id=None,
            disposition=DealDisposition.none,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="Internal Helios weekly standup.",
                    supports="classification",
                )
            ],
        ),
        "mtg_0f3c9a12e8d746b2": _decision(
            classification=MeetingClassification.non_sales,
            outcome=DecisionOutcome.skip,
            company_id=None,
            contact_ids=[],
            deal_id=None,
            disposition=DealDisposition.none,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="Not a sales conversation and not a customer evaluation.",
                    supports="classification",
                )
            ],
            reason_codes=["NON_SALES_CONTEXT"],
        ),
        "mtg_5a18d4c7b2e390fa": _decision(
            company_id="company_1",
            contact_ids=["contact_1"],
            deal_id="deal_1",
            evidence=_selection_evidence(
                "Nina jumped on from her personal calendar after the Apex Enterprise thread.",
                company_id="company_1",
                contact_ids=["contact_1"],
                deal_id="deal_1",
            ),
        ),
        "mtg_9c4e2b71a06d8351": _decision(
            company_id="company_2",
            contact_ids=["contact_3", "contact_2"],
            deal_id="deal_2",
            evidence=_selection_evidence(
                "Apex QBR with Ruth Keene from Apex Health. Chris Hale joined because he sits on a "
                "shared IT steering group. Conversation was the Health pilot: HIPAA, clinicians, "
                "and whether the pilot can stay in a BAA-covered workspace. Not a robotics "
                "Enterprise seats discussion.",
                company_id="company_2",
                contact_ids=["contact_3", "contact_2"],
                deal_id="deal_2",
            ),
        ),
        "mtg_2d7f18a5c9b043e6": _decision(
            company_id="company_4",
            contact_ids=["contact_5", "contact_6"],
            deal_id="deal_6",
            evidence=_selection_evidence(
                "Working session with Elena and Tom at Cobalt Bank on Treasury add-on scoping: "
                "cash-position fields, who owns the weekly Treasury forecast, and how it sits next "
                "to the banking CRM.",
                company_id="company_4",
                contact_ids=["contact_5", "contact_6"],
                deal_id="deal_6",
            ),
        ),
        "mtg_e1b60a34d8c2579f": _decision(
            outcome=DecisionOutcome.review,
            company_id="company_3",
            contact_ids=["contact_4"],
            deal_id=None,
            disposition=DealDisposition.new,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="He wants a third, separate commercial path.",
                    supports="deal",
                )
            ],
            reason_codes=["NEW_RFP_REQUIRES_REVIEW"],
        ),
        "mtg_7a3d91c5e2f048b6": _decision(
            outcome=DecisionOutcome.review,
            company_id=None,
            contact_ids=[],
            deal_id=None,
            disposition=DealDisposition.new,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="Intro with Robin Hayes, who booked from a Gmail address.",
                    supports="classification",
                )
            ],
            reason_codes=["UNRESOLVED_PERSONAL_EMAIL_REQUIRES_REVIEW"],
        ),
        "mtg_c8e25f10a4b396d7": _decision(
            company_id="company_9",
            contact_ids=["contact_11"],
            deal_id="deal_10",
            evidence=_selection_evidence(
                "Morgan Blake joined from Gmail. Conversation was the Plant 3 rollout at Keystone "
                "— steel mill, production supervisors, a forecast for the new line.",
                company_id="company_9",
                contact_ids=["contact_11"],
                deal_id="deal_10",
            ),
        ),
        "mtg_3f91b6a0d2c847e5": _decision(
            company_id="company_1",
            contact_ids=["contact_1", "contact_12"],
            deal_id="deal_1",
            evidence=_selection_evidence(
                "Nina brought Amy Zhou, a contractor from Quill Partners, to help Apex "
                "pressure-test the Enterprise evaluation. Amy is staffed on Apex; Quill is not "
                "evaluating Helios for itself.",
                company_id="company_1",
                contact_ids=["contact_1", "contact_12"],
                deal_id="deal_1",
            ),
        ),
        "mtg_a4c18e92b7d056f3": _decision(
            outcome=DecisionOutcome.review,
            company_id="company_7",
            contact_ids=["contact_9"],
            deal_id=None,
            disposition=DealDisposition.new,
            evidence=[
                Evidence(
                    source=EvidenceSource.notes,
                    quote="Last year's attempt is closed and in the past.",
                    supports="deal",
                )
            ],
            reason_codes=["EXPLICIT_NEW_DEAL"],
        ),
        "mtg_6b2e0d9f1a8347c5": _decision(
            company_id="company_6",
            contact_ids=["contact_8"],
            deal_id="deal_7",
            evidence=_selection_evidence(
                "Working next-step call with Jane Cho at Elm & Co on Elm Onboarding. Agreed next "
                "step is a security working session on Thursday, September 3. Jane is the person "
                "who will take the recommendation through Elm leadership.",
                company_id="company_6",
                contact_ids=["contact_8"],
                deal_id="deal_7",
            ),
            changes=[
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_7",
                    field="next_step",
                    value="Security working session",
                    evidence=[m15_evidence[0]],
                ),
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_7",
                    field="next_step_date",
                    value="2026-09-03",
                    evidence=[m15_evidence[1]],
                ),
                ProposedFieldChange(
                    entity_type="deal",
                    entity_id="deal_7",
                    field="champion",
                    value="Jane Cho",
                    evidence=[m15_evidence[2]],
                ),
            ],
        ),
    }
