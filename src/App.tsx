
import React, {useState} from 'react';
import {StyleSheet, Text, ImageBackground, View, Image} from 'react-native';
import {TVFocusGuideView} from '@amazon-devices/react-native-kepler';
import {Tile} from './components/Tile';
import {tiles} from './data/tiles';
import {PlayerScreen} from './components/PlayerScreen';
import {SettingsScreen} from './components/SettingsScreen';
import {FILM_CATALOGUE} from './data/filmCatalogue';

export const App = ({
  initialShowPlayer = false,
  initialShowSettings = false,
  initialFilmId = 'sintel_trailer',
}: {
  initialShowPlayer?: boolean;
  initialShowSettings?: boolean;
  initialFilmId?: string;
} = {}) => {
  const [focusedTileId, setFocusedTileId] = useState<string>('home');
  const [showPlayer, setShowPlayer] = useState(initialShowPlayer);
  const [showSettings, setShowSettings] = useState(initialShowSettings);
  const [selectedFilmId, setSelectedFilmId] = useState<string>(initialFilmId);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);

  const focusedTile = tiles.find((t) => t.id === focusedTileId);

  if (showSettings) {
    return <SettingsScreen onBack={() => setShowSettings(false)} />;
  }

  if (showPlayer) {
    return (
      <PlayerScreen
        filmId={selectedFilmId}
        onBack={() => setShowPlayer(false)}
      />
    );
  }

  return (
    <ImageBackground
      source={require('./assets/background.png')}
      style={styles.background}>
      <View style={styles.headerArea}>
        {focusedTileId === 'home' ? (
          <>
            <View style={styles.headerTextContainer}>
              <Text style={styles.headerTitle}>Hello Vega,</Text>
              <Text style={styles.headerSubtitle}>
                Use your remote to explore and start your Vega journey 🚀
              </Text>
            </View>
            <Image
              source={require('./assets/vega.png')}
              style={styles.vegaLogo}
              resizeMode="contain"
              testID="vega-logo"
            />
          </>
        ) : (
          <>
            <View style={styles.headerTextContainer}>
              <Text style={styles.focusedTitle}>{focusedTile?.label}</Text>
            </View>
            {focusedTile?.description && (
              <Text style={styles.focusedDescription}>
                {focusedTile.description}
              </Text>
            )}
          </>
        )}
      </View>

      <TVFocusGuideView style={styles.tileRowContent}>
        {tiles.map((tile) => (
          <Tile
            key={tile.id}
            label={tile.label}
            icon={tile.icon}
            isFocused={focusedTileId === tile.id}
            onFocus={() => setFocusedTileId(tile.id)}
            onBlur={() => {}}
            onPress={() => {
              if (tile.id === 'get-started' || tile.id === 'sintel') {
                setSelectedFilmId('sintel');
                setCatalogueError(null);
                setShowPlayer(true);
              } else if (tile.id === 'tears-of-steel') {
                setSelectedFilmId('tears_of_steel');
                setCatalogueError(null);
                setShowPlayer(true);
              } else if (tile.id === 'debug') {
                setShowSettings(true);
              } else if (tile.id === 'learn-more') {
                setSelectedFilmId((prev) =>
                  prev === 'sintel' ? 'tears_of_steel' : 'sintel',
                );
              }
            }}
            testID={`tile-${tile.id}`}
            accessibilityLabel={tile.accessibilityLabel}
            hasTVPreferredFocus={tile.id === 'home'}
          />
        ))}
      </TVFocusGuideView>
    </ImageBackground>
  );
};

const styles = StyleSheet.create({
  background: {
    flex: 1,
    padding: 160,
  },
  headerArea: {
    flex: 3,
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  headerTextContainer: {
    flex: 1,
  },
  headerTitle: {
    color: '#FFFFFF',
    fontSize: 160,
    lineHeight: 170,
    fontWeight: '600',
  },
  headerSubtitle: {
    color: '#FFFFFF',
    fontSize: 50,
  },
  vegaLogo: {
    width: 500,
    height: 350,
    marginLeft: 120,
  },
  focusedTitle: {
    color: '#FFFFFF',
    fontSize: 150,
    lineHeight: 160,
    fontWeight: 'bold',
    width: 560,
  },
  focusedDescription: {
    color: '#FFFFFF',
    fontSize: 60,
    lineHeight: 80,
    flex: 1,
    marginLeft: 50,
    paddingTop: 20,
  },
  tileRowContent: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
  },
});
