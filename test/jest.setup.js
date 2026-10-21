const { BackHandler } = require('@amazon-devices/react-native-kepler');

if (
  BackHandler &&
  BackHandler.addEventListener &&
  typeof BackHandler.addEventListener.mockImplementation === 'function'
) {
  BackHandler.addEventListener.mockImplementation(() => ({
    remove: jest.fn(),
  }));
}
