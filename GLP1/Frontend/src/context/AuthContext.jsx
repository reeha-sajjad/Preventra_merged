import { createContext, useContext, useState, useCallback } from 'react';
import { api } from '../data/api';

const AuthContext = createContext(null);

const TOKEN_KEY = 'glp1_token';
const USER_KEY  = 'glp1_user';

// Portal is a different origin — leaving the app entirely requires a real
// browser navigation, not a React Router route change.
export const PORTAL_URL =
  import.meta.env.VITE_PORTAL_URL ?? 'https://portal-frontend-production-815a.up.railway.app';

const READMISSIONS_URL =
  (import.meta.env.VITE_READMISSIONS_URL ?? 'https://readmission-frontend-production.up.railway.app').replace(/\/$/, '');

/**
 * Called from main.jsx before anything renders. Handles an incoming
 * `#signout=1&chain=...` hop in the shared logout relay: clears this app's
 * token, then forwards the rest of the chain to the next app, or returns to the
 * portal when the chain is empty.
 *
 * Returns true when it has taken over navigation, so main.jsx skips rendering.
 * That matters: `location.replace()` does not stop this document, and rendering
 * with no token would mount RedirectToPortal - painting the loading screen for
 * the duration of the hop and firing a second, competing navigation.
 */
export function receiveSignout() {
  const hash = window.location.hash || '';
  if (!hash.includes('signout=')) return false;
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  if (!params.get('signout')) return false;

  history.replaceState(null, '', window.location.pathname + window.location.search);
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);

  const [nextKey, ...rest] = (params.get('chain') || '').split(',').filter(Boolean);
  const nextUrl = nextKey === 'readmissions' ? READMISSIONS_URL : null;
  // An unknown next hop ends the relay at the portal rather than stranding the
  // user here with no navigation at all.
  window.location.replace(nextUrl
    ? `${nextUrl}/#signout=1${rest.length ? `&chain=${rest.join(',')}` : ''}`
    : PORTAL_URL);
  return true;
}

/**
 * Clear this app's session and hand over to the portal's sign-out relay, which
 * also clears the portal's and Readmissions' copies. Used by Log out, and by
 * the API client when the backend says the session is no longer valid.
 */
export function endSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  window.location.replace(`${PORTAL_URL}/#signout=1&chain=readmissions`);
}

function readIncomingToken() {
  const hash = window.location.hash || '';
  if (!hash.includes('token=')) return null;

  const token = new URLSearchParams(hash.replace(/^#/, '')).get('token');
  if (!token) return null;

  history.replaceState(null, '', window.location.pathname + window.location.search);
  return token;
}

function decodeClaims(token) {
  try {
    return JSON.parse(atob(token.split('.')[1]));
  } catch {
    return null;
  }
}

const _incomingToken = readIncomingToken();

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => {
    if (_incomingToken) return _incomingToken;
    return localStorage.getItem(TOKEN_KEY);
  });

  const [user, setUser] = useState(() => {
    if (_incomingToken) {
      const claims = decodeClaims(_incomingToken);
      if (claims) {
        const incomingUser = {
          id:          claims.sub,
          email:       claims.email,
          role:        claims.role,
          status:      claims.status,
          hospital_id: claims.hospital_id,
          must_change_password: !!claims.must_change_password,
          app_access:  claims.app_access || [],
        };
        localStorage.setItem(TOKEN_KEY, _incomingToken);
        localStorage.setItem(USER_KEY, JSON.stringify(incomingUser));
        return incomingUser;
      }
    }
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      localStorage.removeItem(USER_KEY);
      return null;
    }
  });

  const _persist = (accessToken, userObj) => {
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(USER_KEY, JSON.stringify(userObj));
    setToken(accessToken);
    setUser(userObj);
  };

  const login = useCallback(async (email, password) => {
    const data = await api.login({ email, password });
    _persist(data.access_token, data.user);
    return data;
  }, []);

  const register = useCallback(async (email, password) => {
    const data = await api.register({ email, password });
    _persist(data.access_token, data.user);
    return data;
  }, []);

  // No more local login screen to fall back to — logging out means
  // leaving GLP-1 entirely and going back to the shared Portal.
  //
  // Navigate WITHOUT touching React state. Clearing token/user re-renders into
  // RedirectToPortal, whose effect navigates to the plain portal URL - and that
  // later navigation cancels this one, dropping `#signout`. The portal then
  // never clears its own session and shows the tiles, still signed in. The page
  // is leaving anyway; there is nothing to re-render for.
  const logout = useCallback(() => endSession(), []);

  return (
    <AuthContext.Provider
      // A pending account, or one still on a temporary password, is not signed
      // in as far as this app is concerned: it goes back to the portal, which
      // shows the "waiting for approval" or "set a new password" screen. The
      // backend refuses it data regardless; this only avoids a dead screen.
      value={{ token, user,
               isAuthenticated: !!token && user?.status !== 'pending' && !user?.must_change_password,
               login, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}