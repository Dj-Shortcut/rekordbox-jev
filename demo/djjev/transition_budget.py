"""Shared timing reserves; these are estimates, not measured latency guarantees."""

# Planning stops budgeting a leisurely blend at the same deadline used by
# policy and the execution adapter to give completion priority.
COMPLETION_MARGIN_SECONDS = 30.
LAUNCH_ALIGNMENT_RESERVE_SECONDS = 8.
# A completion gesture may use the remaining time above this cleanup reserve.
FINAL_CLEANUP_RESERVE_SECONDS = 12.

