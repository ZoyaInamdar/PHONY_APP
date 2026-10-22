#!/usr/bin/env node
/**
 * simulate_player.mjs
 *
 * Runs Person A's engine (packagedIndexAdapter and resolveActions) against
 * a packaged index.json to verify runtime player actions.
 *
 * Usage:
 *   node --experimental-strip-types --loader ./scripts/ts_loader.mjs scripts/simulate_player.mjs --index data/index.json --verify-mute
 */

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { adaptPackagedIndex } from '../src/engine/packagedIndexAdapter.ts';
import { resolveActions } from '../src/engine/resolveActions.ts';
import { DEFAULT_VIEWER_OPTIONS } from '../src/engine/types.ts';

const DEFAULT_POLICIES = {
  flashing: { enabled: true, action: 'hide', source: 'visual' },
  loud_spike: { enabled: true, action: 'mute', source: 'audio' },
  gunshot: { enabled: true, action: 'mute', source: 'audio' },
  explosion: { enabled: true, action: 'mute', source: 'audio' },
  scream: { enabled: true, action: 'mute', source: 'audio' },
  profanity: { enabled: true, action: 'mute', source: 'audio' },
  insect: { enabled: false, action: 'hide', source: 'visual' },
  jump_scare: { enabled: false, action: 'warn', source: 'audio_visual' },
  visible_blood: { enabled: false, action: 'hide', source: 'visual' },
};

function parseArgs(args) {
  const result = {
    indexPath: 'data/index.json',
    verifyMute: false,
    timestamps: [],
    jsonOutput: false,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--index' && i + 1 < args.length) {
      result.indexPath = args[++i];
    } else if (arg === '--verify-mute') {
      result.verifyMute = true;
    } else if (arg === '--timestamps' && i + 1 < args.length) {
      result.timestamps = args[++i].split(',').map(s => parseFloat(s.trim()));
    } else if (arg === '--json') {
      result.jsonOutput = true;
    } else if (arg === '--help' || arg === '-h') {
      console.log(`
Usage: node simulate_player.mjs [options]

Options:
  --index <path>        Path to packaged index.json (default: data/index.json)
  --verify-mute         Verify mute activation, holding, and release boundaries
  --timestamps <list>   Comma-separated timestamps in seconds to evaluate
  --json                Output results in JSON format
  -h, --help            Show this help message
`);
      process.exit(0);
    }
  }

  return result;
}

export function runSimulation(indexPath, options = {}) {
  const rawContent = fs.readFileSync(indexPath, 'utf-8');
  const index = JSON.parse(rawContent);

  const cues = adaptPackagedIndex(index, DEFAULT_POLICIES);
  const viewerOptions = { ...DEFAULT_VIEWER_OPTIONS };

  const results = {
    film_id: index.film_id,
    duration_seconds: index.timeline.duration_seconds,
    total_cues: cues.length,
    evaluations: [],
    boundary_verifications: [],
    all_passed: true,
  };

  if (options.timestamps && options.timestamps.length > 0) {
    for (const t of options.timestamps) {
      const eff = resolveActions(t, cues, viewerOptions);
      results.evaluations.push({
        time: t,
        soundGain: eff.soundGain,
        hide: eff.hide,
        skipTo: eff.skipTo,
        activeCueIds: eff.activeCueIds,
      });
    }
  }

  if (options.verifyMute) {
    const audioMuteCues = cues.filter(c => c.source === 'audio' && c.action === 'mute');

    for (const cue of audioMuteCues) {
      const cueReport = {
        cue_id: cue.id,
        start: cue.start,
        end: cue.end,
        checks: [],
        passed: true,
      };

      // 1. Before cue start (if start > 0 and no overlapping cue)
      if (cue.start > 0.05) {
        const tBefore = Math.max(0, cue.start - 0.05);
        const effBefore = resolveActions(tBefore, cues, viewerOptions);
        const isCoveredByOther = cues.some(c => c.id !== cue.id && c.source === 'audio' && c.action === 'mute' && c.start <= tBefore && tBefore < c.end);
        if (!isCoveredByOther) {
          const pass = effBefore.soundGain === 1 && !effBefore.activeCueIds.includes(cue.id);
          cueReport.checks.push({
            point: 'before_start',
            time: tBefore,
            soundGain: effBefore.soundGain,
            expectedGain: 1,
            passed: pass,
          });
          if (!pass) cueReport.passed = false;
        }
      }

      // 2. Exact start boundary [start is INCLUDED in half-open [start, end))
      const effStart = resolveActions(cue.start, cues, viewerOptions);
      const startPass = effStart.soundGain === 0 && effStart.activeCueIds.includes(cue.id);
      cueReport.checks.push({
        point: 'at_start',
        time: cue.start,
        soundGain: effStart.soundGain,
        expectedGain: 0,
        passed: startPass,
      });
      if (!startPass) cueReport.passed = false;

      // 3. Middle of cue
      const tMid = (cue.start + cue.end) / 2.0;
      const effMid = resolveActions(tMid, cues, viewerOptions);
      const midPass = effMid.soundGain === 0 && effMid.activeCueIds.includes(cue.id);
      cueReport.checks.push({
        point: 'mid_cue',
        time: tMid,
        soundGain: effMid.soundGain,
        expectedGain: 0,
        passed: midPass,
      });
      if (!midPass) cueReport.passed = false;

      // 4. Just before end boundary (end - 0.001)
      const tJustBeforeEnd = Math.max(cue.start, cue.end - 0.001);
      const effJustBefore = resolveActions(tJustBeforeEnd, cues, viewerOptions);
      const justBeforePass = effJustBefore.soundGain === 0 && effJustBefore.activeCueIds.includes(cue.id);
      cueReport.checks.push({
        point: 'just_before_end',
        time: tJustBeforeEnd,
        soundGain: effJustBefore.soundGain,
        expectedGain: 0,
        passed: justBeforePass,
      });
      if (!justBeforePass) cueReport.passed = false;

      // 5. Exact end boundary [end is EXCLUDED from half-open [start, end))
      const effEnd = resolveActions(cue.end, cues, viewerOptions);
      const endMutedByAnother = cues.some(c => c.id !== cue.id && c.source === 'audio' && c.action === 'mute' && c.start <= cue.end && cue.end < c.end);
      const expectedEndGain = endMutedByAnother ? 0 : 1;
      const endPass = effEnd.soundGain === expectedEndGain && !effEnd.activeCueIds.includes(cue.id);
      cueReport.checks.push({
        point: 'at_end',
        time: cue.end,
        soundGain: effEnd.soundGain,
        expectedGain: expectedEndGain,
        passed: endPass,
      });
      if (!endPass) cueReport.passed = false;

      // 6. After cue end (if end + 0.05 <= timeline duration)
      const tAfter = cue.end + 0.05;
      if (tAfter < index.timeline.duration_seconds) {
        const effAfter = resolveActions(tAfter, cues, viewerOptions);
        const afterMutedByAnother = cues.some(c => c.source === 'audio' && c.action === 'mute' && c.start <= tAfter && tAfter < c.end);
        const expectedAfterGain = afterMutedByAnother ? 0 : 1;
        const afterPass = effAfter.soundGain === expectedAfterGain;
        cueReport.checks.push({
          point: 'after_end',
          time: tAfter,
          soundGain: effAfter.soundGain,
          expectedGain: expectedAfterGain,
          passed: afterPass,
        });
        if (!afterPass) cueReport.passed = false;
      }

      // 7. Verify mute disabled toggle behavior
      const disabledViewerOptions = { ...viewerOptions, muteEnabled: false };
      const effDisabled = resolveActions(tMid, cues, disabledViewerOptions);
      const disabledPass = effDisabled.soundGain === 1;
      cueReport.checks.push({
        point: 'mute_disabled_check',
        time: tMid,
        soundGain: effDisabled.soundGain,
        expectedGain: 1,
        passed: disabledPass,
      });
      if (!disabledPass) cueReport.passed = false;

      results.boundary_verifications.push(cueReport);
      if (!cueReport.passed) {
        results.all_passed = false;
      }
    }
  }

  return results;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const cliArgs = parseArgs(process.argv.slice(2));
  const simResults = runSimulation(cliArgs.indexPath, {
    verifyMute: cliArgs.verifyMute,
    timestamps: cliArgs.timestamps,
  });

  if (cliArgs.jsonOutput) {
    console.log(JSON.stringify(simResults, null, 2));
  } else {
    console.log(`=== Player Simulation Results: ${simResults.film_id} ===`);
    console.log(`Timeline duration: ${simResults.duration_seconds}s | Total cues: ${simResults.total_cues}`);
    if (simResults.evaluations.length > 0) {
      console.log('\nEvaluations:');
      for (const ev of simResults.evaluations) {
        console.log(`  t=${ev.time.toFixed(3)}s -> soundGain=${ev.soundGain}, hide=${ev.hide}, active=${JSON.stringify(ev.activeCueIds)}`);
      }
    }
    if (simResults.boundary_verifications.length > 0) {
      console.log(`\nBoundary Verifications (${simResults.boundary_verifications.length} mute cues):`);
      for (const bv of simResults.boundary_verifications) {
        const status = bv.passed ? 'PASS' : 'FAIL';
        console.log(`  [${status}] Cue ${bv.cue_id} [${bv.start.toFixed(3)}s - ${bv.end.toFixed(3)}s)`);
        for (const ch of bv.checks) {
          const chStatus = ch.passed ? '✓' : '✗';
          console.log(`     ${chStatus} ${ch.point} @ ${ch.time.toFixed(3)}s: soundGain=${ch.soundGain} (expected ${ch.expectedGain})`);
        }
      }
    }
    console.log(`\nFinal Engine Verification Result: ${simResults.all_passed ? 'ALL CHECKS PASSED' : 'FAILURES DETECTED'}`);
  }

  process.exit(simResults.all_passed ? 0 : 1);
}
