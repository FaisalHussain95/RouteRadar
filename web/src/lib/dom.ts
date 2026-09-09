/** Is any child still off-screen to the *right* of a horizontally scrolling strip?
 *
 * Drives the 16px fade on the carrier chip strip: § Filter bar overflow asks for the fade
 * while chips remain off-screen, so it has to account for how far the strip is already
 * scrolled — otherwise it stays lit at the end of the scroll and the `onScroll` that
 * recomputes it can never change the answer. A pure function of three numbers, so it is
 * testable without a layout engine, which jsdom does not have. */
export function hasOverflow(
  el: Pick<HTMLElement, "scrollWidth" | "clientWidth" | "scrollLeft">,
): boolean {
  return el.scrollWidth - el.clientWidth - el.scrollLeft > 1;
}
