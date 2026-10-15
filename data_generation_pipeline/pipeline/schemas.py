"""Pydantic schemas for every LLM output in the pipeline. The JSON schema of each model is appended to the prompt,
and the model's answer is validated against it (see llm.LLM.json). Keep fields flat and explicit: the generator is a
weaker model than the designer."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ------------------------------------------------------------------ 00 ingest
class CropCheck(BaseModel):
    contains: Literal["figure", "table", "mixed", "none"]
    complete: bool = Field(description="true if the whole figure/table is inside the crop")
    extra_text_top: bool = Field(description="true if body paragraphs (not part of the figure) appear at the top")
    extra_text_bottom: bool = Field(description="true if body paragraphs appear at the bottom")
    cut_top: bool
    cut_bottom: bool
    running_header: bool = Field(default=False, description="true if a page header, page number, author names or running title appears at the very top or bottom")
    note: str = ""


class PickSide(BaseModel):
    pick: Literal["A", "B"]
    reason: str = ""


class ImageOnlyFact(BaseModel):
    statement: str = Field(description="a fact a reader learns only by looking at the image")
    value: str = Field(description="the exact short value string visible in the image, e.g. '27', '12:40', '3rd'")


class VisualFacts(BaseModel):
    description: str = Field(description="2-3 sentences: what the figure shows")
    image_only_facts: list[ImageOnlyFact] = Field(description="3-8 facts readable only from pixels: ranks, labels, timestamps, colours, which curve is highest, etc.")
    text_labels: list[str] = Field(description="short text strings visible inside the image")
    key_numbers: list[str] = Field(description="numbers visible in the image (axis ticks excluded)")


class TableRow(BaseModel):
    label: str
    cells: list[str] = Field(description="one string per column header, '' if empty, '-' if a dash")


class TableGroup(BaseModel):
    group: str = Field(description="row-group label such as a dataset name, '' if the table has no groups")
    rows: list[TableRow]


class TableExtract(BaseModel):
    title: str
    column_headers: list[str] = Field(description="flattened leaf column headers, e.g. 'Test Accuracy'")
    row_groups: list[TableGroup]
    notes: str = ""


# ------------------------------------------------------------------ 01 facts
class ConfigFact(BaseModel):
    name: str = Field(description="what the setting is, e.g. 'RL learning rate'")
    value: str = Field(description="exact value string from the source")
    context: str = Field(description="short phrase locating it, e.g. 'stage 2 GRPO'")


class Claim(BaseModel):
    statement: str
    numbers: list[str] = Field(description="exact number strings the claim rests on")
    source_hint: str = Field(description="table/figure/section that supports it")
    label: str = Field(default="", description="a short noun phrase (3-8 words) naming WHAT the leading number measures, with no digits, no units and no value — e.g. 'share of respondents whose organisation misunderstood the benefits', 'NHS AI Diagnostic Fund allocation'")


class ClaimLabel(BaseModel):
    id: str
    label: str = Field(description="3-8 words, no digits, no units, no value")


class ClaimLabels(BaseModel):
    items: list[ClaimLabel]


class TextFacts(BaseModel):
    method_name: str = Field(description="the name of the proposed system/method")
    one_sentence: str = Field(description="what the method does, in plain words a product team would use")
    task_summary: str = Field(description="the task/problem and how it is evaluated (metrics, datasets)")
    components: list[str] = Field(description="named components, models, datasets, baselines mentioned")
    config_facts: list[ConfigFact]
    claims: list[Claim] = Field(description="6-12 quantitative claims")
    pipeline_steps: list[str] = Field(description="ordered steps of the method as an engineer would list them")


class TableRole(BaseModel):
    table_id: str
    role: Literal["main_results", "ablation_progression", "comparison", "other"]
    progression_order: list[str] = Field(description="row labels in run order, for ablation_progression only")
    release_row: str = Field(description="exact row label of the final configuration, or ''")
    overlaps_with: str = Field(default="", description="table_id of a more detailed table that contains the same configurations (this table is then a summary view), else ''")
    notes: str = ""


class TableRoles(BaseModel):
    tables: list[TableRole]


# ------------------------------------------------------------------ 02 world
class Person(BaseModel):
    id: str = Field(description="lowercase first name, ascii")
    name: str
    role: str
    level: Literal["lead", "senior", "junior", "intern", "exec"]
    owns: str = Field(description="what part of the work this person owns this week")
    voice: str = Field(description="how they write in chat, 1 sentence")
    color: str = Field(description="hex colour for the avatar")


class Channel(BaseModel):
    id: str = Field(description="slug without #, e.g. 'proj-atlas'")
    name: str = Field(description="display name with #")
    private: bool
    members: list[str] = Field(description="person ids")
    purpose: str


class World(BaseModel):
    company_name: str
    tagline: str
    product: str = Field(description="what the company sells, one sentence")
    project_codename: str
    project_sentence: str = Field(description="what the team is building this week, using the source method as the internal system; never mention a publication")
    internal_model_name: str = Field(description="what employees call the system in chat")
    customer_name: str
    customer_contact: str = Field(description="full name of the external contact")
    customer_contact_email: str
    milestone_a: str = Field(description="internal milestone at the end of the week, e.g. 'release-candidate freeze'")
    milestone_b: str = Field(description="external milestone, e.g. 'customer pilot review'")
    people: list[Person]
    channels: list[Channel]
    late_joiner: str = Field(description="person id who joins on the last day")


# ------------------------------------------------------------------ 03 timeline
class RunPlan(BaseModel):
    run_id: str = Field(description="ids with the prefix given in the prompt (e.g. R-00, R-01, … or F-00, F-01, …) in chronological order")
    label: str = Field(description="what configuration this run is, e.g. 'Stage 1 only'")
    dataset: str = Field(description="dataset/benchmark name, or '' if the table has none")
    table_id: str
    row_label: str = Field(description="exact row label in that table")
    row_group: str = Field(description="exact row group (dataset) label in that table, '' if none")
    day_offset: int = Field(description="0 = first day (Wed) … 6 = freeze day (Tue)")
    time: str = Field(description="HH:MM")
    owner: str = Field(description="person id who runs and reports it")
    kind: Literal["baseline", "progression", "release", "analysis"]


class InventedFact(BaseModel):
    id: str = Field(description="X-01, X-02, ...")
    name: str
    value: str = Field(description="the true value as a short string with unit, e.g. '790 ms'")
    owner: str = Field(description="person id who measures/knows it")
    channel: str = Field(description="channel id where it is first posted")
    day_offset: int
    time: str
    rationale: str = Field(description="why the company measures this; plausible for the product")
    related_fact_ids: list[str] = Field(default_factory=list, description="metric/config fact ids it relates to")


class RunsPlan(BaseModel):
    runs: list[RunPlan]
    invented: list[InventedFact]


class ErrorPlan(BaseModel):
    id: str = Field(description="E-01, ...")
    kind: Literal["tracker_typo", "image_cell", "doc_value", "channel_value", "doc_headline", "unfixed_inconsistency"]
    fact_id: str = Field(description="metric fact id (F…), invented fact id (X…) or, for unfixed_inconsistency, an image-only fact id (V…)")
    wrong_value: str = Field(description="the wrong value string; must differ from the true value and look like a plausible slip (transposed digits, wrong column). For doc_headline: the single number that will be put on the slide")
    actor: str = Field(description="person id who makes the mistake")
    caught_by: str = Field(description="person id who notices")
    caught_in: Literal["public", "dm"]
    day_offset_caught: int
    time_caught: str
    day_offset_fixed: int
    time_fixed: str
    note: str = Field(description="one sentence on how the slip happened")


class DecisionPlan(BaseModel):
    id: str = Field(description="D-01, ...")
    title: str
    body: str = Field(description="the decision as it will be recorded, concrete, with values")
    by: str = Field(description="person id")
    day_offset: int
    time: str
    rationale: str = Field(description="why, 1-2 sentences, with the numbers behind it")
    rationale_visibility: Literal["public", "private"]
    private_channel: Optional[str] = Field(default=None, description="private channel id where the rationale is discussed if private")
    related_error_id: Optional[str] = Field(default=None, description="error id this decision resolves or forbids, if any")


class ErrorsDecisions(BaseModel):
    errors: list[ErrorPlan]
    decisions: list[DecisionPlan]


class EmailPlan(BaseModel):
    id: str = Field(description="M-01, ...")
    day_offset: int
    time: str
    from_id: Optional[str] = Field(description="person id, or null when the customer contact writes")
    to_ids: list[str] = Field(description="person ids")
    cc_ids: list[str] = Field(default_factory=list)
    external_to: bool = Field(description="true if addressed to the customer contact")
    purpose: str = Field(description="what the mail is for, 1 sentence, naming the concrete values it carries")
    required_values: list[str] = Field(default_factory=list, description="value strings the mail must contain verbatim")
    attach_doc: Optional[Literal["report_v1", "report_v2", "deck_v1", "deck_v2"]] = None


class EmailsPlan(BaseModel):
    emails: list[EmailPlan]


class FactVersion(BaseModel):
    day_offset: int
    time: str = Field(description="HH:MM")
    value: str = Field(description="the value at this version: a number with unit, a person id (for owner facts), a date/time string, or a short phrase")
    channel: str = Field(description="channel id where this version is stated")
    by: str = Field(description="person id who states it; must be a member of the channel")
    how: Literal["initial", "update", "correction", "reassignment"]
    note: str = Field(description="one sentence: what changed and why (for 'correction': the slip, e.g. 'oh wait, I mixed up the two datasets')")


class EvolvingFact(BaseModel):
    id: str = Field(description="Y-01, Y-02, ...")
    name: str = Field(description="what the fact is, e.g. 'owner of the latency measurement', 'freeze deadline', 'headline dataset for the customer', 'index size'")
    kind: Literal["numeric", "owner", "date", "choice", "text"]
    versions: list[FactVersion] = Field(description="2-3 versions in chronological order; the last one is the final state")


class EvolvingFacts(BaseModel):
    facts: list[EvolvingFact]


class DocArc(BaseModel):
    id: str = Field(description="A-01, A-02, ...")
    kind: Literal["note", "diagram", "memo_table"] = Field(description="note = short internal text note; diagram = a block diagram of the system/pipeline; memo_table = a one-page memo with a small results table")
    title: str = Field(description="document title as a colleague would name it")
    author: str = Field(description="person id")
    channel: str = Field(description="channel id where v1 and v2 are posted")
    day_offset_v1: int
    time_v1: str
    reviewer: str = Field(description="person id who reacts to v1 (must differ from author)")
    review_channel: str = Field(description="channel id where the reviewer reacts: the same channel or a DM between reviewer and author")
    review_kind: Literal["mismatch", "add_figure", "wording"] = Field(description="mismatch: a value in v1 disagrees with the tracker; add_figure: reviewer asks to add a figure; wording: reviewer asks to reword a section")
    day_offset_review: int
    time_review: str
    day_offset_v2: int
    time_v2: str
    wrong_fact_id: str = Field(default="", description="for mismatch: the metric fact id (F…) whose value is wrong in v1")
    wrong_value: str = Field(default="", description="for mismatch: the wrong value shown in v1")
    figure_id: str = Field(default="", description="for add_figure: the figure id to add in v2")
    note: str = Field(description="one sentence: what the document is for")


class DocArcs(BaseModel):
    arcs: list[DocArc]


class ImageName(BaseModel):
    id: str
    name: str = Field(description="natural in-world file name with extension, e.g. pipeline_overview.png")
    title: str = Field(default="", description="for tables only: a short in-world title as a colleague would write it, e.g. 'Reward ablation on the validation set' (never 'Table N', never 'our')")


class ImageNames(BaseModel):
    names: list[ImageName]


class DiagramNode(BaseModel):
    id: str
    label: str


class DiagramEdge(BaseModel):
    src: str
    dst: str
    label: str = ""


class DiagramSpec(BaseModel):
    title: str
    nodes: list[DiagramNode] = Field(description="4-8 nodes")
    edges: list[DiagramEdge]


class AnchorResolve(BaseModel):
    index: int = Field(description="1-based index of the item the question refers to, or 0 if it could refer to none or to several")
    reason: str = ""


class ChatterEvent(BaseModel):
    time: str = Field(description="HH:MM")
    channel: str = Field(description="channel id")
    actor: str = Field(description="person id, must be a member")
    kind: Literal["question", "answer", "comment", "request", "status", "banter"]
    about: str = Field(description="event id from the day it reacts to, or 'general'")
    gist: str = Field(description="what the message says, 1 sentence, no numbers that are not in the referenced event")
    answers: int = Field(default=-1, description="for kind 'answer': the 0-based index (in this list) of the question it answers; -1 otherwise")


class DayChatter(BaseModel):
    events: list[ChatterEvent]


# ------------------------------------------------------------------ 04 assets
class ChartSeries(BaseModel):
    name: str
    values: list[float]
    source: Literal["facts", "invented"] = Field(description="'facts' if every value is a metric fact from the tables, 'invented' for world-only numbers")


class ChartSpec(BaseModel):
    id: str = Field(description="chart_progress, chart_<invented id> ...")
    title: str
    kind: Literal["bar", "line"]
    x_labels: list[str]
    series: list[ChartSeries]
    y_label: str
    threshold: Optional[dict] = Field(default=None, description="{label: str, value: number} for a horizontal SLA/threshold line, or null")
    owner: str = Field(description="person id who makes the chart")
    caption: str = Field(description="one line printed under the chart")
    invented_id: str = Field(default="", description="X-… id if this chart is about an invented fact, else ''")


class ChartSpecs(BaseModel):
    charts: list[ChartSpec]


class DocBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullets", "table", "figure", "big_number", "caption"]
    text: str = Field(default="", description="heading/paragraph/caption text, the big number, or a note under a table/figure")
    items: list[str] = Field(default_factory=list, description="bullet items")
    headers: list[str] = Field(default_factory=list, description="inline table column headers")
    rows: list[list[str]] = Field(default_factory=list, description="inline table rows (strings), same length as headers")
    highlight_rows: list[int] = Field(default_factory=list, description="row indices to highlight")
    table_id: str = Field(default="", description="id of a table image asset to embed instead of inline rows")
    figure_id: str = Field(default="", description="id of a figure/chart asset to embed")
    sub: str = Field(default="", description="sub-text under a big_number")


class DocPage(BaseModel):
    title: str
    blocks: list[DocBlock]


class DocSpec(BaseModel):
    filename: str
    pages: list[DocPage]


# ------------------------------------------------------------------ 05 render
class SlackMsg(BaseModel):
    event_id: str
    time: str = Field(description="HH:MM")
    actor: str
    text: str


class SlackDay(BaseModel):
    messages: list[SlackMsg]


class EmailText(BaseModel):
    subject: str
    body: str


class MeetingNotes(BaseModel):
    title: str
    body: str = Field(description="bullet notes, 4-7 lines, '•' bullets separated by newlines")


class NaturalnessReview(BaseModel):
    score: int = Field(description="1-5, 5 = reads like real coworkers")
    problems: list[str]
