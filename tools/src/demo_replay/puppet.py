"""Puppet agent — thin wrapper for Ed25519 agent identity.

Re-exports DemoAgent as PuppetAgent for clarity. The underlying
implementation is the same: in-memory keypair, JWS signing, and
auth header generation.
"""
from __future__ import annotations

from demo_replay.wallet import DemoAgent

# PuppetAgent is just DemoAgent with a clearer name
PuppetAgent = DemoAgent
