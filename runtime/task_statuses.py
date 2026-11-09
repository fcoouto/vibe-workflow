"""Canonical task states shared by runtime controls and presentation."""
STATES = frozenset({'TRIAGE','WORKING','REVIEW','WAITING','PAUSED','DONE','CANCELLED'})
ATTENTION_STATES = frozenset({'REVIEW', 'WAITING'})
# Retired on 9 November 2026: work moves from TRIAGE straight to WORKING, and BLOCKED became WAITING.
# Records and titles written by earlier runtimes may still hold them; they are read as history, never written.
RETIRED_STATES = frozenset({'READY', 'BLOCKED'})
