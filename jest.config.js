/** @type {import('jest').Config} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  // Restrict to the engine and subtitles test suites only.
  // The React Native app tests use jest.config.json (react-native preset).
  testMatch: [
    '<rootDir>/test/**/*.test.ts',
    '<rootDir>/test/**/*.spec.ts',
  ],
  transform: {
    '^.+\\.tsx?$': ['ts-jest', { tsconfig: 'tsconfig.engine.json' }],
  },
  clearMocks: true,
  collectCoverageFrom: [
    'src/engine/**/*.ts',
    'src/subtitles/**/*.ts',
    '!src/**/*.d.ts',
  ],
  coverageDirectory: 'coverage',
  testPathIgnorePatterns: [
    '/node_modules/',
    '/coverage/',
  ],
  // AJV (used by validation.ts) keeps an open handle after all tests
  // finish, preventing Jest from exiting on its own. forceExit ensures
  // the process terminates once all test suites have completed.
  forceExit: true,
};
