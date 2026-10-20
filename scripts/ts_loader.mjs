/**
 * Custom ESM loader for Node 24 to support:
 * 1. Extensionless TypeScript imports (e.g. from './types' to './types.ts')
 * 2. Runtime stub exports for TypeScript type/interface declarations
 *    imported without 'type' keyword in Person A engine files.
 */
import { pathToFileURL } from 'node:url';

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (err) {
    if (specifier.startsWith('./') || specifier.startsWith('../')) {
      for (const ext of ['.ts', '.js', '/index.ts', '/index.js']) {
        try {
          return await nextResolve(specifier + ext, context);
        } catch {
          // Continue trying other candidate extensions
        }
      }
    }
    throw err;
  }
}

export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);
  if (url.endsWith('/types.ts') || url.endsWith('\\types.ts')) {
    const dummyExports = `
export const Cue = {};
export const CueAction = {};
export const CueSource = {};
export const CueStatus = {};
export const ViewerOptions = {};
export const EffectiveActions = {};
export const SubtitleCue = {};
export const TimedCue = {};
export const CueKind = {};
export const FlaggedTerm = {};
export const FlaggedWordSummary = {};
export const RawDetection = {};
export const SubtitlesParseResult = {};

`;
    return {
      format: result.format,
      shortCircuit: true,
      source: (result.source || '') + dummyExports,
    };
  }
  if (url.endsWith('/recap.ts') || url.endsWith('\\recap.ts')) {
    const dummyExports = `
export const AppliedAction = {};
export const PlaybackSessionPayload = {};
`;
    return {
      format: result.format,
      shortCircuit: true,
      source: (result.source || '') + dummyExports,
    };
  }
  let source = result.source ? String(result.source) : '';
  if (source.includes('require(') && !source.includes('__customCreateRequire')) {
    source = `import { createRequire as __customCreateRequire } from 'node:module';\nconst require = __customCreateRequire(import.meta.url);\n` + source;
    return {
      format: result.format,
      shortCircuit: true,
      source,
    };
  }
  return result;
}
