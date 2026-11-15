"""Node labels, relationship types, and the hierarchy used for progressive expansion."""
from __future__ import annotations

NODE_LABELS = {
    "Earth", "Continent", "Country", "DemocracyAssessment", "ContactRule", "GovernmentLevel",
    "GovernmentBody", "PoliticalOffice", "Person", "PoliticalParty", "Term", "Source",
}

EDGE_TYPES = {
    "CONTAINS", "HAS_ASSESSMENT", "HAS_CONTACT_RULE", "HAS_GOVERNMENT_LEVEL", "HAS_BODY", "HAS_OFFICE",
    "HOLDS_OFFICE", "CONTESTS_OFFICE", "MEMBER_OF_PARTY", "SUPPORTED_BY", "HAS_TERM", "TERM_HELD_BY",
}

# (edge type, direction) pairs that define a node's children when expanding in the UI.
# direction "out": (node)-[TYPE]->(child); "in": (child)-[TYPE]->(node)
CHILD_RULES: dict[str, list[tuple[str, str]]] = {
    "Earth": [("CONTAINS", "out")],
    "Continent": [("CONTAINS", "out")],
    "Country": [("HAS_ASSESSMENT", "out"), ("HAS_CONTACT_RULE", "out"), ("HAS_GOVERNMENT_LEVEL", "out")],
    "GovernmentLevel": [("CONTAINS", "out"), ("HAS_BODY", "out")],
    "GovernmentBody": [("HAS_OFFICE", "out")],
    "PoliticalOffice": [("HOLDS_OFFICE", "in"), ("CONTESTS_OFFICE", "in")],
    "Person": [("MEMBER_OF_PARTY", "out")],
    "PoliticalParty": [],
    "DemocracyAssessment": [("SUPPORTED_BY", "out")],
    "ContactRule": [("SUPPORTED_BY", "out")],
    "Term": [("SUPPORTED_BY", "out")],
    "Source": [],
}

# The parent edge used to walk from any hierarchy node back up to Earth.
PARENT_RULES: dict[str, list[tuple[str, str]]] = {
    "Continent": [("CONTAINS", "in")],
    "Country": [("CONTAINS", "in")],
    "DemocracyAssessment": [("HAS_ASSESSMENT", "in")],
    "ContactRule": [("HAS_CONTACT_RULE", "in")],
    "GovernmentLevel": [("HAS_GOVERNMENT_LEVEL", "in"), ("CONTAINS", "in")],
    "GovernmentBody": [("HAS_BODY", "in")],
    "PoliticalOffice": [("HAS_OFFICE", "in")],
    "Person": [("HOLDS_OFFICE", "out"), ("CONTESTS_OFFICE", "out")],
    "PoliticalParty": [("MEMBER_OF_PARTY", "in")],
    "Term": [("HAS_TERM", "in")],
    "Source": [("SUPPORTED_BY", "in")],
}

SEARCHABLE_LABELS = ["Country", "Person", "PoliticalOffice", "PoliticalParty", "GovernmentBody", "GovernmentLevel"]
