import {Config} from '@remotion/cli/config';

// H.264 MP4 output; overwrite renders of the same name.
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
Config.setChromiumOpenGlRenderer('angle');
