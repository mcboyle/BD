# Offline capture analysis — usage

This document explains how to use the offline capture-ingestion tool, and, just as
importantly, what its output does and does not mean. The tool exists to take capture
artifacts that already sit on disk and run the framework's existing analysis over them
without any network access, producing reports you can read and a suggested corpus entry
you can review. It is deliberately a thin layer: it does no detection of its own, instead
reusing the modules that already implement goal selection, candidate scoring, signing
recognition, temporal drift, and perturbation checks. Understanding that thinness is the
key to reading its output correctly, because the tool can only conclude what those
underlying analyses can conclude, and it is careful never to claim more.

## What the tool is for

The purpose is to digest local captures into the analysis pipeline offline. You point it at
one or more capture artifacts — a `.json` export, a `.wacz` archive, or a directory
containing them — and it normalizes each into a common internal model, runs the appropriate
analysis, and writes a set of reports. If you give it two or more captures that share a
content identity, it recognizes them as a temporal series and runs the temporal harness over
them. If you give it a baseline and a perturbed capture along a named axis, it runs the
perturbation harness on that pair. In every case it produces an inventory of what it
ingested, an analysis of what the framework found, a readiness report classifying the
findings, and, when the analysis yields something corpus-worthy, a suggested corpus entry
for you to review.

## How to run it

The simplest invocation points the tool at one or more captures and an output directory.
Suppose you have two captures of the same title and want to see whether the framework's
behaviors hold across them. You would run the tool with both capture paths and an `--out`
directory, and it would detect the shared identity, run the temporal harness, and write the
reports there. The example command is:

    python tools/offline_capture_analyze.py capture_one.json capture_two.wacz --out ./offline_out

If instead you have a directory full of capture artifacts that form an ordered series, you
can point the tool at the directory and add the `--series` flag, which tells it to treat the
captures as an ordered same-title series even if you want to be explicit about it:

    python tools/offline_capture_analyze.py ./my_captures --series --out ./offline_out

To exercise a perturbation axis, you supply a baseline capture, a perturbed capture, and the
axis they differ on, which must be either `player_config` or `workflow`. These three options
must be given together, and the tool will refuse to proceed if any is missing, because a
perturbation analysis is meaningless without all three. The example command is:

    python tools/offline_capture_analyze.py --baseline base.wacz --perturbed perturbed.wacz --axis player_config --out ./offline_out

In all cases the tool prints a short summary to the console — how many reports it wrote,
that the posture check passed, and that the corpus was not written — and leaves the full
detail in the report files.

## The reports it produces

The tool writes up to five files. The capture inventory describes each artifact it ingested:
the host, the request count, the masked media goal, which optional signals the capture
carries, the redaction state across its requests, and the signing markers present by name.
The offline analysis records what the framework found: the goal selection, the identity and
rendition slots, the candidate scoring, and the signing recognition. The drift report appears
when a temporal series or a perturbation pair was analyzed, and it records the harness verdicts
axis by axis. The validation readiness report is the one to read most carefully, because it
classifies every result into the six categories described below. And the suggested corpus
entry appears as a JSON file when the analysis produced something corpus-worthy, but it is a
suggestion only, never a corpus write.

## How to read the validation readiness report

The readiness report sorts findings into six labels, and the labels mean exactly what they
say. A confirmed finding is something the analysis established directly — an identity slot
observed in the goal, or a drift axis the harness returned as confirmed on real captures. A
possible finding is suggestive but not conclusive, such as a perturbation result that leans
one way without settling the question. An unsupported conclusion is something you might be
tempted to infer but the evidence does not support, and the tool names these explicitly so you
do not draw them — title uniqueness and generalization beyond the captures provided are always
listed here, because no single set of captures can establish them. Validation-ready evidence is
evidence that could update the corpus after human review, such as a real perturbation outcome
that bears on open validation debt. Insufficient evidence is an axis the analysis could not
resolve, most commonly signing drift on captures whose values were scrubbed, or the floor lift
on a series of only two captures. And the required next capture names what evidence would close
each insufficient item, so the report ends by telling you what to collect next.

## What it can and cannot conclude offline

This is the part worth dwelling on. The tool can conclude, from a single capture, what goal
the framework selects, how it scores the candidate slots, what it identifies as the content
identity and the rendition, and what signing markers are present. From a same-identity series
it can conclude whether identity is invariant, whether rendition drifts and is correctly
attributed, and whether the structure is stable — the same temporal behaviors the harness
measures anywhere. From a real perturbation pair it can record how the framework's assumptions
respond to a changed player configuration or workflow, with the verdict left to the data.

What it cannot conclude is equally definite, and the tool is built to refuse these rather than
fudge them. It cannot measure signing drift when the signing values were scrubbed at capture
time; in that case it reports signing as untested, never as absent, because unmeasured is not
the same as unchanged. It cannot establish that an identity value is unique to its title rather
than shared, because that needs a different test than any series provides. It cannot generalize
from the captures you gave it to the framework's behavior universally. And it cannot, under any
circumstances, retire validation debt or write the corpus — the most it does is emit a suggested
entry for you to review, an entry that carries no resolution pointer and an unconfirmed outcome,
so that even if you accept it at face value it cannot close an open question on its own.

## The posture guarantees

The tool operates strictly within the recognition-only posture, and several of its behaviors
exist specifically to enforce that. It never fetches, replays, reconstructs, or computes signing
material; it reads local files and classifies them. Every media goal it displays is shown as a
masked path template with the query dropped and the path templated, so that neither a query
signing value nor a path-embedded one can appear in a report. Signing is reported by marker name
and type only, never by value. The temporal harness compares values by an in-memory fingerprint
and the tool surfaces only its boolean drift verdicts, never a fingerprint. And as a safety net,
the tool scans every report it generates for any signing value that might have escaped masking,
and if it finds one it fails the run before writing anything, rather than emitting a report with
a leak in it. These are not promises in prose; they are checks in the code, and the build's own
posture gate confirms the tool ships without any redaction-disabling capability.
