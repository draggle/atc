/**
 * The bottom row: Frequency · the chat bar · Sent by squack, all three the same height, along the
 * bottom edge. One place so the two side boxes, the right rail above them and the answer dock all
 * agree; change it here and the row stays a row.
 */

/** Height of the two side boxes, and the height the answer dock grows up to. Fits an 800px screen. */
export const BOTTOM_ROW_H = 240;

/** Gap from the window edge, in px (Tailwind's `-2`). */
export const EDGE = 8;

/** Top of the row, measured from the bottom of the window. */
export const ROW_TOP = BOTTOM_ROW_H + EDGE;

/**
 * Where the answer dock's own bottom sits: clear of the chat bar, which rests at `bottom-6`.
 * The dock grows up from here to ROW_TOP and never past it.
 */
export const DOCK_BOTTOM = 80;

/** Width of a side box. Narrows with the window so it never runs under the centred chat bar. */
export const SIDE_W = "min(360px, calc(50vw - 356px))";

/** Characters revealed per second as squack's answer streams in. A two-line reply takes ~1.5 s. */
export const STREAM_CPS = 50;
