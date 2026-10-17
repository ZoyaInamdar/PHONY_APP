// Jest manual mock for @react-native-async-storage/async-storage
//
// Provides an in-memory asynchronous key-value store for Jest tests
// so that components and storage utilities can persist, read, and clear
// state without native device binaries or ESM parsing issues.

'use strict';

const store = new Map();

const asyncStorageMock = {
  getItem: jest.fn(async (key) => store.get(key) ?? null),
  setItem: jest.fn(async (key, value) => {
    store.set(key, String(value));
  }),
  removeItem: jest.fn(async (key) => {
    store.delete(key);
  }),
  multiRemove: jest.fn(async (keys) => {
    keys.forEach((key) => store.delete(key));
  }),
  removeMany: jest.fn(async (keys) => {
    keys.forEach((key) => store.delete(key));
  }),
  clear: jest.fn(async () => {
    store.clear();
  }),
};

module.exports = asyncStorageMock;
module.exports.default = asyncStorageMock;
module.exports.__esModule = true;
