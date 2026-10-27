"""Evals for openfrontdesk: an LLM plays the customer, the real engine plays the front desk."""

from ofd_evals.customer import CustomerTurn, Persona, SimulatedCustomer, SimulatorError
from ofd_evals.environment import EvalEnvironment, eval_environment
from ofd_evals.runner import ConversationRecord, TurnRecord, run_conversation

__all__ = [
    "ConversationRecord",
    "CustomerTurn",
    "EvalEnvironment",
    "Persona",
    "SimulatedCustomer",
    "SimulatorError",
    "TurnRecord",
    "eval_environment",
    "run_conversation",
]
