"""Bounded recovery budgets and typed failures; never authorizes replayed input."""
import time


class HealthBlocked(RuntimeError):
    """A condition that cannot safely be repaired inside the active session."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NativeConnectionError(RuntimeError):
    """Transport evidence distinguishes no connection from possibly sent input."""
    def __init__(self, role, command, sent, cause):
        super().__init__(f'{role}/{command}: verbinding onderbroken ({type(cause).__name__}).')
        self.role, self.command = role, command
        self.commands_sent = sent
        self.dispatched = sent  # True means possibly dispatched, never verified.
        self.code = 'native_reply_unknown' if sent else 'native_unreachable'


class RecoveryBudget:
    """Session-wide caps prevent an intermittent fault from retrying forever."""
    def __init__(self, maximum, name):
        self.maximum, self.name, self.used = maximum, name, 0

    def consume(self):
        if self.used >= self.maximum:
            raise HealthBlocked(self.name + '_exhausted',
                                f'Herstellimiet bereikt ({self.name}); bediening onderbroken.')
        self.used += 1
        return self.used
