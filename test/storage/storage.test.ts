import {
  appendBotheredLog,
  getBotheredLog,
  clearEverything,
  STORAGE_KEYS,
} from '../../src/storage/index';
import AsyncStorage from '@react-native-async-storage/async-storage';

describe('Storage Module', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
  });

  it('caps the bothered log at 100 entries', async () => {
    // Generate 105 mock entries
    for (let i = 0; i < 105; i++) {
      await appendBotheredLog({
        timestamp: i,
        activeTrigger: 'flash',
        loggedAt: Date.now(),
      });
    }

    const log = await getBotheredLog();
    expect(log.length).toBe(100);
    // The oldest 5 entries should be evicted, so the first timestamp should be 5
    expect(log[0].timestamp).toBe(5);
  });

  it('clearEverything wipes the log', async () => {
    await appendBotheredLog({
      timestamp: 1,
      activeTrigger: 'flash',
      loggedAt: Date.now(),
    });
    await clearEverything();
    const log = await getBotheredLog();
    expect(log.length).toBe(0);
  });
});
