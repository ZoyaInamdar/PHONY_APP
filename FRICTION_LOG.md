# TriggerAware — Friction Log

**Project:** Trigger-Aware Player for Amazon Vega OS / Fire TV  
**Tool evaluated:** Kiro, used for AI-assisted coding, repository changes, test development, and Vega application scaffolding  
**Purpose:** Document development friction, observed outcomes, workarounds, and actionable improvements.

---

## Friction 1: Timestamp Synchronization on Variable-Frame-Rate Video

**Task attempted:**  
Synchronize timestamped visual detections from the offline Python pipeline with playback actions in the React Native player.

**Steps taken:**
1. Initially used frame-count-based timestamp calculations such as `frame_count / fps`.
2. Investigated timing drift when processing variable-frame-rate (VFR) media.
3. Examined the need to use presentation timestamps from the original media timeline.
4. Focused on timestamp validation and consistent cue packaging between detection and playback.

**Expected result:**  
Generated `[start, end)` intervals should align with the original media timeline so that visual protections and audio actions occur at the intended moments.

**Actual result:**  
Frame-count-based calculations can produce timing drift on VFR media because a single nominal frame rate does not necessarily represent every frame's actual presentation time. The reported development experience included protection actions firing out of sync with the media.

**Severity:** High — timing errors can cause protection actions to occur at the wrong point in a film.

**Workaround used:**  
Use media presentation timestamps where available, preserve timestamps in media-relative seconds, and validate cue intervals before packaging them for playback.

**Actionable suggestion:**  
Document timestamp sources and conversion rules, add VFR test fixtures, and compare generated cue times against known events on the original media timeline.

**Evidence status:** The timing issue was reported during development. Verify the current ingestion implementation and any claimed millisecond-level accuracy before treating the fix as complete.

---

## Friction 2: Balancing Jump-Scare Detection and Computational Cost

**Task attempted:**  
Develop a jump-scare detector that combines audio and visual evidence while keeping the offline detection pipeline practical to run.

**Steps taken:**
1. Considered a 3D convolutional neural network (CNN) for modelling temporal visual context.
2. Evaluated the computational cost of continuous video-model inference.
3. Considered audio evidence such as loudness spikes alongside visual evidence such as sudden visual onsets.
4. Explored combining audio and visual detections according to their temporal proximity.

**Expected result:**  
The detector should identify likely jump scares using complementary audio and visual signals without requiring unnecessarily expensive continuous 3D video-model inference.

**Actual result:**  
A 3D CNN was considered computationally expensive for the project's minimum viable implementation. Audio loudness spikes or visual changes alone can also produce false positives, so the detector needed a way to combine evidence from both modalities.

**Severity:** High — excessive computational cost or unreliable classification could undermine practical testing and the quality of jump-scare cues.

**Workaround used:**  
Explore lightweight audio-visual fusion using temporal proximity between audio and visual events instead of relying on continuous 3D CNN inference.

**Actionable suggestion:**  
Evaluate the fusion method using labelled positive and negative examples, measure false positives and false negatives, and document the actual thresholds and scoring formula used by the implementation.

**Evidence status:** The lightweight fusion approach is described in the project challenges. Verify the actual temporal tolerance and confidence-score formula in code before documenting exact values as implemented.

---

## Friction 3: Temporal Flicker and False Positives in Visible-Blood Detection

**Task attempted:**  
Produce stable `visible_blood` cue intervals from frame-based visual detections while avoiding false positives from visually similar content.

**Steps taken:**
1. Examined how inconsistent frame-level detections could cause trigger intervals to open and close repeatedly.
2. Identified temporary occlusions and changes in model confidence as possible causes of unstable detections.
3. Identified red clothing, neon lighting, and similar visual content as useful negative test cases.
4. Considered temporal smoothing to bridge brief detection gaps and reduce isolated detections.

**Expected result:**  
The detector should produce stable intervals for relevant events without repeatedly opening and closing cues because of brief detection failures. Visually similar objects and lighting should not automatically be treated as blood.

**Actual result:**  
Frame-by-frame detection can fluctuate when the target is briefly occluded or the model's confidence changes. Visually similar objects and lighting can also produce false positives, potentially causing distracting playback actions.

**Severity:** High — unstable or incorrect detections can cause unnecessary blackouts and reduce viewer trust.

**Workaround used:**  
Use temporal smoothing where implemented and keep experimental detection behavior clearly identified until it has been adequately evaluated.

**Actionable suggestion:**  
Add labelled positive and negative test footage, measure false-positive and false-negative rates, and verify that smoothing does not create excessively long or inaccurate cue intervals.

**Evidence status:** The challenge and proposed smoothing approach were reported during development. Verify the exact frame thresholds, buffer duration, and experimental status handling in the current code before claiming those details as implemented.

---

## Friction 4: Integrating Heterogeneous Detectors Through a Shared Contract

**Task attempted:**  
Combine visual, audio, and subtitle-based detections into one cue format that the player can consume consistently.

**Steps taken:**
1. Reviewed the information needed to represent detector events.
2. Established a shared five-field detection contract.
3. Used a common packaging and validation workflow to convert detections into timestamped cues.
4. Kept detector implementation separate from player action resolution.

**Expected result:**  
All detector outputs should map to a shared record containing `start`, `end`, `trigger`, `score`, and `source`, with validated timestamps and recognized trigger names.

**Actual result:**  
Different detectors can produce different identifiers, timestamp representations, score meanings, and metadata. Without a consistent boundary, these differences can cause packaging, validation, or runtime integration problems.

**Severity:** High — inconsistent records can prevent valid detections from reaching the player or cause them to be interpreted incorrectly.

**Workaround used:**  
Normalize detector output at the shared integration boundary and validate records before generating the packaged index.

**Actionable suggestion:**  
Maintain contract tests for every detector, validate trigger registration and interval semantics, and document the meaning of each detector's score without assuming every score is a calibrated probability.

**Lesson learned:**  
Agreeing on shared interfaces early reduces integration friction and allows different team members to develop detectors independently.

---

## Friction 5: Per-Trigger Personalization and Vega OS Playback Compatibility

**Task attempted:**  
Connect configurable protection actions to individual triggers while preserving predictable playback behavior on Amazon Vega OS.

**Steps taken:**
1. Reviewed the relationship between trigger configuration, cue data, and playback actions.
2. Identified overlapping cues and correct restoration timing as important test cases.
3. Considered television-friendly navigation and fallback behavior for visual protection.
4. Kept playback action handling separate from offline model inference.

**Expected result:**  
Viewers should be able to configure supported actions for different triggers, and the player should apply those actions consistently while handling overlapping cues and restoring normal playback state.

**Actual result:**  
Per-trigger customization requires coordination between configuration, persistent settings, cue processing, and action resolution. Television interfaces also introduce remote-navigation requirements, while some visual effects may need a simpler fallback on the target platform.

**Severity:** High — inconsistent settings or action timing can reduce usability and cause protection behavior to differ from what viewers selected.

**Workaround used:**  
Preserve the existing action-resolution pipeline, validate cue data before playback, and use supported fallback behavior where platform capabilities are limited.

**Actionable suggestion:**  
Add per-trigger settings persistence tests, overlapping-cue regression tests, and end-to-end playback tests on the Vega Virtual Device and representative media.

---

## Friction 6: Kiro Scaffold Generation Overwrote Existing Configuration

**Task attempted:**  
Generate a Vega-compatible React Native scaffold inside the existing TriggerAware repository.

**Steps taken:**
1. Generated scaffold files in the repository root using Kiro.
2. Reviewed the generated `package.json` and `tsconfig.json`.
3. Identified that these files already contained project configuration.
4. Manually merged the required scaffold settings and retained the original versions in Git history for comparison and recovery.

**Expected result:**  
Scaffold generation would add the required Vega files while preserving existing application configuration, scripts, dependencies, and TypeScript settings.

**Actual result:**  
The generated output overwrote existing configuration files. Manual merging was needed to restore the intended configuration and incorporate the necessary scaffold changes.

**Severity:** High — potentially destructive to the existing project configuration.

**Workaround used:**  
Manually merge the required scaffold settings and use the original Git versions for comparison and recovery.

**Actionable suggestion:**  
Provide a non-destructive scaffold-generation mode that previews conflicting files, writes generated output into a separate directory by default, and requires explicit confirmation before replacing existing files. The tool should also produce a clear diff of proposed changes.

**Evidence status:** Reported in the October 15, 2026 review. Verify the final merged configuration before submission.

---

## Friction 7: Machine-Specific Paths in the Deployment Workflow

**Task attempted:**  
Build and launch the Vega application using the generated deployment script.

**Steps taken:**
1. Inspected the deployment workflow and the paths used to locate the Vega CLI.
2. Reviewed the expected package-output path.
3. Identified hardcoded absolute paths associated with another developer's macOS environment.
4. Investigated the reported package path containing an `undefined` directory segment.

**Expected result:**  
The deployment workflow would use the SDK installation and generated package paths available on the current developer's machine.

**Actual result:**  
The script contained machine-specific paths that were not portable to the current environment. The expected package path also contained an `undefined` segment, making it difficult to rely on the script without further investigation.

**Severity:** High — the script was not portable and could fail on another developer's machine.

**Workaround used:**  
Defer reliance on the script until the SDK location and actual package-output path are confirmed.

**Actionable suggestion:**  
Provide a portable deployment-script template that discovers the Vega CLI, validates required environment variables, locates generated packages, and reports a clear error when the package is missing.

**Evidence status:** The paths were reported during repository review. The exact cause of the `undefined` segment was not established.

---

## Friction 8: Virtual Device Discovery Timing

**Task attempted:**  
Discover the running Amazon Vega Virtual Device using `vega device list`.

**Steps taken:**
1. Started the Virtual Device.
2. Ran the device-list command immediately after startup.
3. Repeated the check after waiting approximately 30 seconds.

**Expected result:**  
The device would be available to the CLI as soon as startup completed, or the tooling would provide a clear readiness state.

**Actual result:**  
The command reportedly did not work immediately after startup but worked after approximately 30 seconds.

**Severity:** Medium — delayed readiness caused uncertainty in the deployment sequence and could lead developers to mistake a startup delay for a failed setup.

**Workaround used:**  
Wait for the Virtual Device to initialize before listing devices or attempting deployment.

**Actionable suggestion:**  
Provide a documented readiness check or polling command with a timeout, and distinguish between a device that is still starting, a device that is unavailable, and a genuine CLI failure.

**Evidence status:** The timing behavior was observed in the reported workflow. A specific internal cause or officially acknowledged Vega CLI defect was not established.

---

## Friction 9: Jest Process Appeared to Hang

**Task attempted:**  
Run the Jest suite and determine why the test process did not exit as expected.

**Steps taken:**
1. Investigated the test process and terminal output.
2. Used the `--detectOpenHandles` option to investigate whether open handles explained the behavior.
3. Added the Jest `forceExit` option as a workaround.

**Expected result:**  
The test command would complete, report its results, and exit cleanly without requiring forced termination.

**Actual result:**  
The terminal experience appeared to hang or stop providing output. The `--detectOpenHandles` run did not identify an open handle that explained the behavior. Some occurrences may have reflected terminal-tool output truncation rather than a genuinely stuck Jest process.

**Severity:** Medium — it was harder to distinguish a completed test run from a stalled process.

**Workaround used:**  
Use `forceExit` to allow the test process to terminate, while treating this as a workaround rather than proof that the underlying cause was identified.

**Actionable suggestion:**  
Improve test-runner diagnostics and terminal integration so developers can distinguish a completed test suite, an active process, and an output-stream problem. Document when forced process exit is appropriate and the trade-offs it introduces.

**Evidence status:** The process behavior was reported. Ajv was not established as the root cause, and the issue was not proven to be pre-existing.

---

## Friction 10: Scaffold Test Files Were Outside the Reported Test Run

**Task attempted:**  
Validate the generated Vega scaffold and the existing project tests.

**Steps taken:**
1. Reviewed the reported Jest results.
2. Inspected the scaffold test files `App.spec.tsx` and `Tile.spec.tsx`.
3. Compared the test files with the reported default `npm test` run.

**Expected result:**  
The reported test command would make it clear which relevant test files were included in validation.

**Actual result:**  
The two scaffold test files were reportedly not included in the default `npm test` run that produced the 93-test result.

**Severity:** Medium — developers could incorrectly assume that the scaffold tests had been validated.

**Workaround used:**  
Identify the relevant test files explicitly and decide whether they should be included in the standard test command or executed separately.

**Actionable suggestion:**  
Make test discovery and coverage scope more visible in the standard test workflow. Scaffold-generated test files should either be included automatically or clearly identified as separate validation steps.

**Evidence status:** Reported in the October 15, 2026 review. Verify the current Jest configuration and test scripts before final submission.

---

## Friction 11: Contract Acceptance Was Confused With Runtime Implementation

**Task attempted:**  
Extend the engine contract to represent the required visual actions and modalities.

**Steps taken:**
1. Added `dim` and `blur` as action values and `text` and `audio_visual` as modality values.
2. Extended `EffectiveActions` with a `visualAction` field.
3. Updated the adapter to carry the new values.
4. Rewrote four existing adapter tests that had expected `dim` and `blur` to throw.

**Expected result:**  
The engine would preserve the newly agreed contract values, while the application would implement the associated user-visible behavior.

**Actual result:**  
The adapter was changed to carry the new values faithfully, and the tests were changed to reflect that contract. However, `dim` and `blur` were not yet rendered by the application at the time of the October 15 review.

**Severity:** Medium — the contract could appear complete even when the corresponding feature was not implemented end to end.

**Workaround used:**  
Separate engine-contract tests from player-rendering tests, and obtain agreement from the teammate responsible for the shared contract before accepting changes to existing test expectations.

**Actionable suggestion:**  
Encourage AI-assisted coding tools to distinguish contract changes, adapter behavior, UI implementation, and end-to-end verification in their completion summaries. Tests that encode an earlier contract should be reviewed explicitly before being rewritten.

**Evidence status:** Reported in the October 15 review. Assess later player implementation using the current code rather than this historical status.

---

## Friction 12: Reproducible YAMNet ONNX Runtime Setup

**Task attempted:**  
Run the YAMNet audio-classification model used by TriggerAware to detect audio events.

**Steps taken:**
1. Investigated the model-loading error.
2. Identified a compatibility issue between the model's ONNX IR version and the initially used ONNX Runtime environment.
3. Checked the Python environments and installed runtime versions.
4. Used the project's virtual environment with a compatible ONNX Runtime version.
5. Verified that the model could load and run inference in the corrected environment.

**Expected result:**  
The model should load successfully in the project's Python environment and perform inference on audio input.

**Actual result:**  
The model initially encountered a compatibility issue because the installed runtime environment did not support the model's required ONNX IR version.

**Severity:** High — the issue blocked audio-model inference until the environment was corrected.

**Workaround used:**  
Use the project's virtual environment with a compatible ONNX Runtime installation instead of relying on the incompatible ambient Python environment.

**Actionable suggestion:**  
Document the supported Python and ONNX Runtime versions, pin compatible dependencies, and add a startup validation or smoke test that checks model loading before running the audio-detection pipeline.

**Lesson learned:**  
A model file alone is not sufficient for reproducible inference. Runtime compatibility and environment consistency must be checked as part of model integration.

---

## Friction 13: Distinguishing Contract and Test Success From Complete Functionality

**Task attempted:**  
Use AI-assisted development to implement shared engine changes, validate the project, and generate a Vega application scaffold.

**Steps taken:**
1. Used Kiro to coordinate changes across related TypeScript types and adapter code.
2. Added integration tests that read the project's `data/index.json` and use trigger policies exported from `config.yaml`.
3. Ran the reported automated tests, type checking, validators, and `git diff --check`.
4. Built a Vega package, installed the AArch64 package, and observed a successful application-launch message.

**Expected result:**  
The shared contract, application scaffold, and related tests would be aligned, with the test and launch results providing clear evidence of the project's actual working state.

**Actual result:**  
The reported checks passed: 93 Jest tests across eight suites, 71 Python tests, TypeScript type checking, both project validators, and `git diff --check`. The generated package was also reported as installed and launched in the Virtual Device. However, those results did not independently prove that every action, modality, UI component, or end-to-end playback path was fully functional.

**Severity:** Medium — overly broad interpretations of successful tests or builds could hide unimplemented behavior or missing test coverage.

**Workaround used:**  
Review file diffs, inspect test scope, validate runtime behavior separately, and distinguish completed features from contract-only changes or planned functionality.

**Actionable suggestion:**  
Require AI-assisted development tools to label claims as verified, inferred, or unverified and provide supporting command output or file diffs. Treat scaffold generation, successful package launch, unit tests, and end-to-end player validation as separate milestones.

**Evidence status:** Test counts and package-launch results are taken from the recorded October 15 review. They should be replaced or confirmed with current results before submission.

---

## 4. Accuracy and Evidence Lessons

The project experience highlighted the importance of checking AI-generated explanations against actual tool output.

- The Jest behavior did not establish Ajv as the root cause.
- The Virtual Device delay demonstrated a timing issue in the observed workflow, not a confirmed internal Vega CLI defect.
- Tests written after implementation should not be described as test-first development.
- Rewriting tests to reflect a new contract can be appropriate, but the responsible teammate should agree with the changed behavior.
- A successful package build and launch demonstrate packaging progress, not complete playback functionality.
- A configuration warning should not automatically be described as a compiler defect without evidence.
- A detector producing output does not by itself establish real-world accuracy.
- A shared contract accepting a value does not prove that the player implements the corresponding effect.

**Actionable suggestion:**  
AI-assisted development tools should label statements as verified, inferred, or unverified, and provide relevant command output or file diffs supporting claims about build success, root causes, and test coverage.

---

## 5. Remaining Verification and Follow-Up

Before treating the work as ready for release, the team should:

1. Review `git diff .gitignore` and verify that media, model, and secret exclusions—including `*.mp4`, `data/media/`, `models/`, and `.env`—remain correct.
2. Review the final `package.json` and `tsconfig.json` changes against their original Git versions.
3. Confirm the current staging and commit status; the October 15 review reported that no changes were staged or committed at that time.
4. Verify whether `App.spec.tsx` and `Tile.spec.tsx` are included in the current test workflow.
5. Re-run `scripts/export_trigger_policies.py` whenever `config.yaml` changes, then execute the integration tests against the regenerated fixture.
6. Verify the current implementation of `dim`, `blur`, caption censorship, and `audio_visual` behavior instead of relying on historical status notes.
7. Confirm the actual current player UI and Vega runtime behavior.
8. Inspect the existing `friction-log.md` and `feedback.md` files before updating them.
9. Verify the current VFR timestamp handling and any claimed millisecond-level precision.
10. Confirm the exact jump-scare fusion thresholds and confidence-score formula from the implementation.
11. Confirm the visible-blood detector's actual temporal-smoothing behavior and negative-test coverage.
12. Re-run the relevant tests and record current command output before making new claims about passing tests or working functionality.

---

## 6. Overall Assessment

Kiro provided practical value by accelerating cross-file contract changes, test development, and Vega scaffolding. The recorded test results and successful installation and launch of a generated package demonstrate concrete development progress.

The main friction was not simply that the AI made mistakes. It was that generated changes could overwrite existing configuration, explanations could state unverified causes too confidently, and test or package success could be interpreted more broadly than the evidence justified.

The most effective workflow was therefore to use Kiro for implementation and iteration while retaining human responsibility for reviewing diffs, preserving original files, checking test scope, validating platform behavior, and obtaining agreement on shared interfaces.

**Would we use Kiro again?** Yes, for accelerating implementation, scaffolding, and test development, provided that generated changes are reviewed before acceptance, original files can be recovered, and all completion claims are checked against actual test output and runtime behavior.

**Evidence limitation:** This log combines reported project challenges with the recorded October 15, 2026 Kiro development review. It is not an independent reproduction of all reported incidents. Before submission, update it to reflect the current repository, current test output, and any later fixes.
