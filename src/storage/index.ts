import AsyncStorage from '@react-native-async-storage/async-storage';

// Define shared storage keys to prevent typos across the app
export const STORAGE_KEYS = {
  BOTHERED_LOG: '@phony_app_bothered_log',
  USER_SETTINGS: '@phony_app_user_settings',
  USER_PROFILE: '@phony_app_user_profile',
};

// --------------------------------------------------------
// CORE STORAGE UTILS (For Person B's Settings/Profile)
// --------------------------------------------------------

export async function setItem(key: string, value: any): Promise<void> {
  try {
    const jsonValue = JSON.stringify(value);
    await AsyncStorage.setItem(key, jsonValue);
  } catch (e) {
    console.error(`Error saving to storage key ${key}`, e);
  }
}

export async function getItem<T>(key: string): Promise<T | null> {
  try {
    const jsonValue = await AsyncStorage.getItem(key);
    return jsonValue != null ? JSON.parse(jsonValue) : null;
  } catch (e) {
    console.error(`Error reading from storage key ${key}`, e);
    return null;
  }
}

export async function clearEverything(): Promise<void> {
  try {
    // This explicitly wipes the log, settings, and profile all at once
    const keys = Object.values(STORAGE_KEYS);
    if (typeof (AsyncStorage as any).multiRemove === 'function') {
      await (AsyncStorage as any).multiRemove(keys);
    } else if (typeof (AsyncStorage as any).removeMany === 'function') {
      await (AsyncStorage as any).removeMany(keys);
    } else if (typeof (AsyncStorage as any).clear === 'function') {
      await (AsyncStorage as any).clear();
    } else {
      await Promise.all(keys.map((k) => AsyncStorage.removeItem(k)));
    }
  } catch (e) {
    console.error('Error clearing all app data', e);
  }
}

// --------------------------------------------------------
// "THAT BOTHERED ME" LOG UTILS (For Person C)
// --------------------------------------------------------

export interface BotheredLogEntry {
  timestamp: number;
  activeTrigger: string;
  loggedAt: number;
}

export async function appendBotheredLog(entry: BotheredLogEntry): Promise<void> {
  try {
    const currentLog = (await getItem<BotheredLogEntry[]>(STORAGE_KEYS.BOTHERED_LOG)) || [];
    currentLog.push(entry);

    // Strictly cap the log at the last 100 entries
    const cappedLog = currentLog.slice(-100);

    await setItem(STORAGE_KEYS.BOTHERED_LOG, cappedLog);
  } catch (e) {
    console.error('Error appending to bothered log', e);
  }
}

export async function getBotheredLog(): Promise<BotheredLogEntry[]> {
  return (await getItem<BotheredLogEntry[]>(STORAGE_KEYS.BOTHERED_LOG)) || [];
}
