// Focus point (fraction of the screenshot) and zoom for each line, in line order.
// Measured from the app window (Window widgets mapped to the grab), the same for both languages within 3%.
export const FOCUS: [number, number, number][] = [
  [0.5, 0.5, 1],      // whole window
  [0.15, 0.73, 2.6],  // tempo card
  [0.45, 0.16, 3],    // the orange "1" marker
  [0.28, 0.2, 2.4],   // the clicks
  [0.41, 0.73, 2.6],  // count-in card
  [0.61, 0.79, 2.8],  // "Fill pauses with hi-hat"
  [0.77, 0.58, 2.8],  // backing track, guitar muted
  [0.7, 0.47, 3],     // Speed in the transport bar
  [0.77, 0.62, 2.8],  // backing track, vocals
  [0.5, 0.5, 1],      // whole window
];
