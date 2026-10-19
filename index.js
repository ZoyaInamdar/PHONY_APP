import React from 'react';
import { AppRegistry, LogBox } from 'react-native';
import { App } from './src/App';
import { name as appName } from './app.json';

// Temporary workaround for problem with nested text
// not working currently.
LogBox.ignoreAllLogs();

const Root = () => <App initialShowPlayer={true} />;

AppRegistry.registerComponent(appName, () => Root);
