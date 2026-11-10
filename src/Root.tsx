import React from 'react';
import {Composition, staticFile} from 'remotion';
import {getAudioDurationInSeconds} from '@remotion/media-utils';
import {AnimeShort, AnimeShortProps} from './AnimeShort';

export const FPS = 30;
export const WIDTH = 1080; // production render; local proof renders use --scale=0.6667 → 720x1280
export const HEIGHT = 1920;

export const defaultPilotProps: AnimeShortProps = {
  title: 'Black Clover S2 Episode 7 "Beyond Despair" Explained in Hindi | Noelle vs Megicula Full Fight',
  scriptText:
    'Kya aap soch sakte ho ki ek insaan apni sabse badi takleef ke saamne bhi ruk jaaye? Noelle ne aaj wahi kar dikhaya hai.',
  audioFile: 'voiceover.mp3',
  // No scene images bundled for the local pilot default → v1 gradient fallback.
  // CI passes real generated scenes (public/scenes/scene-N.jpg) via props.json.
  scenes: [],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="AnimeShort"
      component={AnimeShort}
      durationInFrames={FPS * 60}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={defaultPilotProps}
      calculateMetadata={async ({props}) => {
        // Composition length follows the generated voiceover exactly (+1s tail).
        const audioSeconds = await getAudioDurationInSeconds(staticFile(props.audioFile));
        return {
          durationInFrames: Math.ceil(audioSeconds * FPS) + FPS,
          props,
        };
      }}
    />
  );
};
