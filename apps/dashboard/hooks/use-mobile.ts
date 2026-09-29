import * as React from "react"

const MOBILE_BREAKPOINT = 768

// useSyncExternalStore instead of a useEffect+setState pair: it is the
// React-recommended way to read a browser-only value like this (see
// https://react.dev/reference/react/useSyncExternalStore), and avoids the
// extra post-mount render the effect-based version required.
function subscribe(callback: () => void) {
  const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
  mql.addEventListener("change", callback)
  return () => mql.removeEventListener("change", callback)
}

function getSnapshot() {
  return window.innerWidth < MOBILE_BREAKPOINT
}

function getServerSnapshot() {
  return false
}

export function useIsMobile() {
  return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}
