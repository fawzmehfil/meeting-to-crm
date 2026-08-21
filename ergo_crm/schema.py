FIELD_SCHEMA = {
    "deal": {
        "next_step": {"type": "string", "write": "overwrite"},
        "next_step_date": {"type": "date", "write": "overwrite"},
        "competitor": {
            "type": "enum",
            "write": "overwrite",
            "values": ["Gong", "Clari", "Chorus", "Outreach", "Other"],
        },
        "champion": {"type": "string", "write": "write-once"},
        "risk": {
            "type": "enum",
            "write": "overwrite",
            "values": ["low", "medium", "high"],
        },
    },
    "contact": {
        "title": {"type": "string", "write": "write-once"},
        "role_in_deal": {
            "type": "enum",
            "write": "write-once",
            "values": ["champion", "economic_buyer", "influencer", "blocker", "unknown"],
        },
    },
    "company": {
        "industry": {"type": "string", "write": "write-once"},
        "employee_band": {
            "type": "enum",
            "write": "write-once",
            "values": ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001+"],
        },
    },
}

ENTITY_TYPES = ("company", "contact", "deal")
FINDABLE_FIELDS = {("contact", "email"), ("company", "domain")}
RELATIONS = {
    "contact_company": frozenset({"contact", "company"}),
    "deal_company": frozenset({"deal", "company"}),
    "deal_contact": frozenset({"deal", "contact"}),
}
RELATION_FOR_TYPES = {types: name for name, types in RELATIONS.items()}
SEARCH_LIMIT_CAP = 10
