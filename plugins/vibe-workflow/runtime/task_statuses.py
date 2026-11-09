"""Canonical task states shared by runtime controls and presentation."""
STATES = frozenset({'TRIAGE','READY','WORKING','REVIEW','WAITING','BLOCKED','PAUSED','DONE','CANCELLED'})
ATTENTION_STATES = frozenset({'REVIEW', 'WAITING', 'BLOCKED'})
