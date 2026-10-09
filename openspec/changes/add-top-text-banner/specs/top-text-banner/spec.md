# top-text-banner Specification

## Purpose
Burns an operator-supplied text strip along the full width of the top edge of
a clip (present for the clip's whole duration), with font-size / text-colour /
background-tint controls, configurable both before cutting (Create recipe) and
per clip afterwards (Edit modal, bulk apply, publish/export).

## ADDED Requirements

### Requirement: Full-width top text strip
The system SHALL provide a compose layer that renders the operator's text as
one or two centered lines on a translucent strip spanning the full video width
at the very top edge, present for the entire clip. Text SHALL wrap to at most
two lines. The layer is only applied when its toggle is enabled and the text is
non-blank.

#### Scenario: Burning a meeting title across every clip
- **WHEN** the operator enables the top text banner with text
  "St. Johns BOCC Public comments - SB 686" and processes a job
- **THEN** every composed clip carries that text centred in a full-width strip
  at the top edge for its full duration

#### Scenario: Blank text disables the layer
- **WHEN** the top text toggle is enabled but no text is supplied
- **THEN** the layer is skipped and the clip renders unchanged

### Requirement: Example text pre-filled as the default
The system SHALL pre-fill the top-banner text field with a real meeting-title
example ("St. Johns BOCC Public comments - SB 686") as its default value
everywhere the field seeds (Create recipe, per-clip Edit modal, publish/export
previews), even while the banner itself stays OFF until the operator enables
it. The operator edits the copy to their actual title. Enabling the toggle does
not clear the pre-filled text.

#### Scenario: Fresh toggle carries the example
- **WHEN** the operator turns the top banner on without typing anything
- **THEN** the text field already holds "St. Johns BOCC Public comments - SB 686"
  and that text is burned when the clip is composed

#### Scenario: Banner stays off by default
- **WHEN** a new job/clip is created with the top banner untouched
- **THEN** the banner is disabled even though the text field is pre-filled

### Requirement: Font size, text colour and background tint controls
The system SHALL let the operator choose a font size (S/M/L, scaled relative to
video width), a text colour (hex), whether a background strip is shown behind
the text, and when shown its tint colour and opacity. Colour hex values and
strip opacity SHALL be validated on the backend.

#### Scenario: Custom size, colour and tint
- **WHEN** the operator sets size L, text colour #FFFFFF and a semi-transparent
  black strip
- **THEN** the rendered text is proportionally large, white, and sits on a
  translucent dark band spanning the full width

#### Scenario: Invalid colour is rejected
- **WHEN** a non-hex colour value is sent for the text colour or strip colour
- **THEN** the request is rejected and no clip is composed

### Requirement: Recipe, per-clip, bulk and publish/export propagation
The system SHALL let the operator configure the top text banner once on the
Create recipe (applied to every clip that job generates), override or enable it
per clip in the Edit modal, propagate it via apply-to-all and the multi-select
bulk editor (text is shared config, copied verbatim, unlike per-clip hook
text), and honour it on per-clip and bulk download/export and publish.

#### Scenario: Create-recipe default carries into the Edit modal
- **WHEN** the operator enables the top text banner with text + style on the
  Create recipe and a clip is generated
- **THEN** the clip's edit modal opens with the top text toggle on and the
  recipe text + style pre-filled

#### Scenario: Bulk edit applies the shared banner
- **WHEN** the operator bulk-edits several selected clips with the top text
  banner on
- **THEN** every selected clip is recomposed with that text and style

#### Scenario: Publish composes with the banner
- **WHEN** a clip whose top text banner is enabled is published with
  compose-first
- **THEN** the upload carries the freshly composed video with the top text strip
