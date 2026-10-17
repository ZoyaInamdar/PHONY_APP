export function censorText(text: string, flaggedWords: string[]): string {
  if (!flaggedWords || flaggedWords.length === 0) return text;
  
  let censored = text;
  flaggedWords.forEach(word => {
    // Match the exact word with word boundaries to avoid partial matches
    const regex = new RegExp(`\\b${word}\\b`, 'gi');
    censored = censored.replace(regex, (match) => {
      if (match.length <= 2) return match[0] + '*';
      return match[0] + '*'.repeat(match.length - 2) + match[match.length - 1];
    });
  });
  return censored;
}