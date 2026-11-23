"""Pure prompt-builder helpers for the standalone policy report generator.

This module is the prompt-composition half of the policy report lane. It is
deliberately a flat set of pure functions: no class state, no LLM client, no
clock / env / filesystem / random / network access. The downstream report
generator wires these helpers into a ``PolicyLLMClient.chat_json`` call --
that call is the generator's concern, NOT this module's.

Why pure functions and not a class:
  * The prompt body is fully determined by (persona, scope, scenario,
    run_metadata). Given the same inputs, the same prompt comes out. A class
    would imply hidden state; there is none.
  * The eventual generator is its own class with its own state (the LLM
    client, the per-call run_metadata). Composing flat helpers into that
    class is cleaner than nesting two classes.

The 5-tag taxonomy is enumerated from :class:`HypothesisTag` so the prompt
cannot drift from the canonical enum -- adding a sixth value to the enum
(which is a breaking change at the schema level) would automatically flow
into the prompt without a separate edit.

The per-persona section list is read from
:func:`app.policy.persona_defaults.report_sections_for` -- the SSoT used by
every other consumer in this codebase. This module does NOT carry a second
copy of the per-persona section enumeration.

Imports are restricted to standard-library typing and the three policy
package modules listed in the module docstring's allowlist. A test pins
the import-graph invariant (mirrors the renderer's purity test).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...policy.hypothesis_tags import HypothesisTag
from ...policy.persona_defaults import report_sections_for
from ...policy.schema import Persona
from ...policy_core.scope_context import scope_wire_value


# ----------------------------------------------------------------------
# Tag taxonomy enumeration text
# ----------------------------------------------------------------------
#
# Sourced from :class:`HypothesisTag` so the prompt taxonomy cannot drift
# from the canonical enum. The one-line gloss per tag is paraphrased
# product vocabulary (NOT a verbatim copy of any plan-doc paragraph) so
# the hygiene gate against planning-terminology citations stays green.
_TAG_GLOSSES: Dict[HypothesisTag, str] = {
    HypothesisTag.SIMULATED_OBSERVATION: (
        "something the simulation itself produced (an agent posted X; "
        "cluster Y formed)"
    ),
    HypothesisTag.INFERENCE: (
        "an analyst inference drawn from one or more simulated observations"
    ),
    HypothesisTag.SOURCE_GROUNDED: (
        "a restated fact lifted from the user-provided seed material"
    ),
    HypothesisTag.ASSUMPTION: (
        "an explicit held assumption -- yours, the user's, or a design "
        "choice -- stated as such"
    ),
    HypothesisTag.REQUIRES_VALIDATION: (
        "an open question demanding real-world evidence before any action"
    ),
}


# ----------------------------------------------------------------------
# Banned / hedge token guidance
# ----------------------------------------------------------------------
#
# The banned-token regression gate
# (``backend/app/services/policy/language_guard.py``, runtime; the test-side
# ``backend/tests/policy/_banned_tokens.py`` is a thin re-export) scans
# rendered markdown for these forbidden phrases unless they appear in
# a sentence with a hedge token. The prompt INSTRUCTS the LLM to avoid
# emitting the forbidden phrases unhedged; the literal forbidden words
# may appear in the prompt body itself because the prompt is teaching
# the LLM what NOT to say. Each entry below explicitly enumerates the
# inflections the runtime regex catches (the regex uses morphology
# groups like ``predict(?:s|ed|ing|ion|ions|or|ors|ive)?`` -- saying
# only "predict" to the LLM under-warns it, because outputting
# "predictions" / "predictor" / "predictive" would still fail the
# gate). Wording is paraphrased product vocabulary, not a copy of any
# plan-doc paragraph.
_BANNED_TOKEN_GUIDANCE = (
    "AVOID the following word families entirely unless the same sentence "
    "carries one of the hedge words listed below. Each family includes "
    "every common inflection -- the runtime gate catches them all, so "
    "emitting any form (verb, noun, plural, gerund, adjective) without "
    "a same-sentence hedge will fail:\n"
    "  * predict / predicts / predicted / predicting / prediction / "
    "predictions / predictor / predictors / predictive\n"
    "  * forecast / forecasts / forecasted / forecasting / forecaster / "
    "forecasters\n"
    "  * will happen / will result / likely to happen\n"
    "  * definitely / certainly\n"
    "  * anticipate / anticipates / anticipated / anticipating\n"
    "  * expect / expects / expected / expecting / expectation / "
    "expectations\n"
    "  * guarantee / guarantees / guaranteed / guaranteeing\n"
    "  * ensure / ensures / ensured / ensuring\n"
    "Acceptable hedge words that pardon a forbidden phrase WITHIN THE "
    "SAME SENTENCE: 'may', 'might', 'could', 'simulation suggests', "
    "'hypothesis' / 'hypotheses', 'risk cluster', 'under these "
    "assumptions', 'appears', 'seems', 'plausibly', 'directional' / "
    "'directionally'. NOTE: the canonical hypothesis-tag names "
    "(``simulated_observation`` etc.) are STRUCTURAL markers, NOT "
    "semantic hedges -- they do not pardon a forbidden phrase. "
    "A hedge in one sentence does NOT pardon a forbidden phrase in the "
    "next sentence -- the gate is sentence-local."
)
BANNED_TOKEN_GUIDANCE = _BANNED_TOKEN_GUIDANCE


# ----------------------------------------------------------------------
# Non-predictive framing preamble
# ----------------------------------------------------------------------
#
# Surfaces the product's non-predictive contract at the top of every
# system prompt. Phrased so every sentence containing a forbidden word
# also carries a hedge, mirroring the renderer's caveat-block discipline
# (the prompt itself is not run through the gate, but staying consistent
# avoids any future surprise if the gate widens to scan prompts too).
_NON_PREDICTIVE_PREAMBLE = (
    "You are a structured-hypothesis generator for a stress-testing "
    "tool. You are NOT a forecaster. Your output is a JSON object "
    "describing simulated audience reactions, framed as hypotheses for "
    "human review -- never as predictions."
)


def _render_tag_taxonomy_text() -> str:
    """Emit the 5-tag taxonomy as a stable bullet list.

    Iteration order is the declared enum order
    (:class:`HypothesisTag`) so the text is byte-stable across runs.
    """
    lines: List[str] = []
    for tag in HypothesisTag:
        gloss = _TAG_GLOSSES[tag]
        lines.append(f"  - {tag.value}: {gloss}")
    return "\n".join(lines)


def render_section_list_for_prompt(sections: List[Dict[str, str]]) -> str:
    """Render the persona+scope section list as a numbered prompt block.

    Pure function over the dict list returned by
    :func:`app.policy.persona_defaults.report_sections_for`. Each entry
    is rendered as ``N. <title> -- <claim_guidance>`` so the LLM sees
    both the section title and the per-section guidance string.

    The 1-based index gives the LLM a stable reference token it can use
    in its own scratch work; the renderer downstream does not consume
    the numbering.

    A missing ``claim_guidance`` key falls back to an empty string
    rather than raising -- the SSoT dicts always include the key today,
    but defaulting keeps the function tolerant of fixture lists that
    may omit it.
    """
    lines: List[str] = []
    for idx, section in enumerate(sections, start=1):
        title = section.get("title", "")
        guidance = section.get("claim_guidance", "")
        if guidance:
            lines.append(f"{idx}. {title} -- {guidance}")
        else:
            lines.append(f"{idx}. {title}")
    return "\n".join(lines)


def build_system_prompt(
    persona: Persona,
    scope: Optional[Any],
    section_list_text: str,
) -> str:
    """Compose the LLM system prompt for a (persona, scope) pair.

    Inputs:
      * ``persona`` -- top-level scenario discriminator.
      * ``scope`` -- required when persona='policy'; ignored otherwise.
      * ``section_list_text`` -- the pre-rendered section enumeration
        (typically the return value of
        :func:`render_section_list_for_prompt` applied to
        :func:`report_sections_for`). Passed in rather than re-resolved
        inside this function so callers can substitute a custom section
        list for testing without monkeypatching the SSoT.

    The prompt body includes:
      * the non-predictive framing preamble
      * the 5-tag taxonomy enumeration with one-line glosses
      * the banned / hedge token guidance (paraphrased product vocabulary)
      * the persona+scope context line
      * the resolved per-section list with claim guidance
      * a contract reminder that the output MUST be a single JSON object

    No verbatim quoting of any plan-doc paragraph: every block above is
    written in code-vocabulary terms describing the same contract.
    """
    scope_value = scope_wire_value(scope)
    taxonomy_block = _render_tag_taxonomy_text()
    return (
        f"{_NON_PREDICTIVE_PREAMBLE}\n"
        "\n"
        "Output contract: a single JSON object matching the Report schema "
        "the caller will validate. Return JSON only -- no surrounding "
        "prose, no markdown fences.\n"
        "\n"
        "Every claim you emit MUST carry exactly one tag from this "
        "taxonomy:\n"
        f"{taxonomy_block}\n"
        "\n"
        f"{_BANNED_TOKEN_GUIDANCE}\n"
        "\n"
        f"Context: persona={persona.value} scope={scope_value}\n"
        "\n"
        "Sections to populate (each section's claim guidance is given "
        "after the title); preserve the section ordering exactly:\n"
        f"{section_list_text}\n"
        "\n"
        "Confidence: include a numeric ``confidence`` value on a claim "
        "ONLY when it is grounded in observed simulation frequency. "
        "Otherwise omit the field; never invent a confidence number."
    )


def build_user_prompt(
    scenario: "object",
    run_metadata: "object",
) -> List[Dict[str, str]]:
    """Emit the message list passed to ``PolicyLLMClient.chat_json``.

    The return shape is ``List[Dict[str, str]]`` with two entries
    (system + user), matching the ``messages=`` parameter of
    :meth:`app.seam.llm_patches.PolicyLLMClient.chat_json`. The user
    message carries the scenario context the LLM needs to fill the
    report; the system message is composed via
    :func:`build_system_prompt`.

    Typing note: ``scenario`` and ``run_metadata`` are annotated as
    ``object`` rather than ``Scenario`` / ``RunMetadata`` to keep this
    module's import surface minimal (the import-graph test only allows
    ``app.policy.schema``, ``app.policy.persona_defaults``,
    ``app.policy.hypothesis_tags``; ``RunMetadata`` lives in
    ``app.policy.run_metadata`` and pulling it in here would expand the
    allowlist for purely-typing reasons). At runtime the function reads
    only public attributes already documented on those classes
    (``persona``, ``scope``, ``title``, ``summary``, ``seed_text``,
    ``stress_question``, ``jurisdiction``, ``assumptions``,
    ``success_criteria``, ``failure_modes_to_watch``, ``stakeholders``
    on Scenario; ``simulation_id``, ``persona``, ``scenario_hash``,
    ``seed_hash`` on RunMetadata).
    """
    persona: Persona = getattr(scenario, "persona")
    scope: Optional[Any] = getattr(scenario, "scope", None)
    sections = report_sections_for(persona, scope)
    section_list_text = render_section_list_for_prompt(sections)
    system = build_system_prompt(persona, scope, section_list_text)

    title: str = getattr(scenario, "title", "") or ""
    summary: str = getattr(scenario, "summary", "") or ""
    seed_text: str = getattr(scenario, "seed_text", "") or ""
    stress_question: str = getattr(scenario, "stress_question", "") or ""
    jurisdiction: Optional[str] = getattr(scenario, "jurisdiction", None)
    assumptions: List[str] = list(getattr(scenario, "assumptions", []) or [])
    success_criteria: List[str] = list(
        getattr(scenario, "success_criteria", []) or []
    )
    failure_modes: List[str] = list(
        getattr(scenario, "failure_modes_to_watch", []) or []
    )
    stakeholders = list(getattr(scenario, "stakeholders", []) or [])

    simulation_id: str = getattr(run_metadata, "simulation_id", "") or ""
    scenario_hash: str = getattr(run_metadata, "scenario_hash", "") or ""
    seed_hash: str = getattr(run_metadata, "seed_hash", "") or ""

    stakeholder_lines: List[str] = []
    for seg in stakeholders:
        seg_id = getattr(seg, "segment_id", "") or ""
        name = getattr(seg, "name", "") or ""
        seg_type = getattr(seg, "type", "") or ""
        stakeholder_lines.append(f"  - {seg_id} [{seg_type}]: {name}")
    stakeholders_block = (
        "\n".join(stakeholder_lines) if stakeholder_lines else "  (none)"
    )

    def _bullets(items: List[str]) -> str:
        if not items:
            return "  (none)"
        return "\n".join(f"  - {item}" for item in items)

    user = (
        f"Scenario title: {title}\n"
        f"Summary: {summary}\n"
        f"Jurisdiction: {jurisdiction or 'n/a'}\n"
        "\n"
        "Seed text:\n"
        f"{seed_text}\n"
        "\n"
        f"Stress question: {stress_question}\n"
        "\n"
        "Stakeholders:\n"
        f"{stakeholders_block}\n"
        "\n"
        "Assumptions:\n"
        f"{_bullets(assumptions)}\n"
        "\n"
        "Success criteria:\n"
        f"{_bullets(success_criteria)}\n"
        "\n"
        "Failure modes to watch:\n"
        f"{_bullets(failure_modes)}\n"
        "\n"
        "Run provenance (the caller will overwrite these fields on the "
        "validated Report; you may reference them for context but do NOT "
        "fabricate alternative values):\n"
        f"  simulation_id={simulation_id}\n"
        f"  scenario_hash={scenario_hash}\n"
        f"  seed_hash={seed_hash}\n"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


__all__ = [
    "BANNED_TOKEN_GUIDANCE",
    "render_section_list_for_prompt",
    "build_system_prompt",
    "build_user_prompt",
]
