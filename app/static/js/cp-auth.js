/**
 * cp-auth.js — Channel Partner Portal: Authentication
 *
 * Contains:
 *  - handleGoogleLogin() — Google Sign-In OAuth callback
 *  - doLogin()           — email + client_secret login
 *  - doLogout()          — clear session and return to login screen
 *
 * API endpoints used:
 *  POST /api/v1/reseller/auth/google-login  (Google OAuth)
 *  POST /api/v1/reseller/auth/email-login   (email + secret)
 *
 * To debug login issues: check the network tab for these two endpoints.
 * Common errors:
 *  401 → wrong email/secret
 *  403 → email not registered as a channel partner / account suspended
 */

'use strict';


// =============================================================================
// GOOGLE SIGN-IN CALLBACK
// =============================================================================

/**
 * Called automatically by Google Identity Services after the user
 * clicks the "Sign in with Google" button.
 */
async function handleGoogleLogin(response) {
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';

  try {
    const resp = await fetch(`${BASE}/reseller/auth/google-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: response.credential }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      errEl.textContent = data.detail || 'Google login failed. Your email may not be registered as a channel partner.';
      errEl.style.display = 'block';
      return;
    }

    // Save token and reseller info
    _token      = data.access_token;
    _resellerId = data.reseller_id;

    // Decode email from JWT payload (base64 middle section)
    try {
      const payload = JSON.parse(atob(data.access_token.split('.')[1]));
      _partnerEmail = payload.email || '';
    } catch (_) { _partnerEmail = ''; }

    // Persist session so page refresh doesn't log out
    sessionStorage.setItem(PORTAL_KEY, JSON.stringify({
      token: _token, resellerId: _resellerId, email: _partnerEmail,
    }));

    showApp();

  } catch (err) {
    errEl.textContent = 'Connection error. Is the server running at localhost:8000?';
    errEl.style.display = 'block';
  }
}


// =============================================================================
// EMAIL + SECRET LOGIN
// =============================================================================

/**
 * Called when the partner clicks "Sign In" in the fallback form.
 * Uses email address + client_secret (the sec_xxx string).
 */
async function doLogin() {
  const email  = (document.getElementById('login-email')?.value || '').trim();
  const secret = (document.getElementById('login-secret')?.value || '').trim();
  const errEl  = document.getElementById('login-error');
  const btnText = document.getElementById('login-btn-text');
  const spinner = document.getElementById('login-spinner');

  errEl.style.display = 'none';

  if (!email || !secret) {
    errEl.textContent = 'Please enter your email and client secret.';
    errEl.style.display = 'block';
    return;
  }

  // Show loading state on button
  btnText.style.display = 'none';
  spinner.style.display = 'block';
  document.getElementById('btn-login').disabled = true;

  try {
    const resp = await fetch(`${BASE}/reseller/auth/email-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, client_secret: secret }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      errEl.textContent = data.detail || 'Login failed. Check your credentials.';
      errEl.style.display = 'block';
      return;
    }

    // Save token and reseller info
    _token        = data.access_token;
    _resellerId   = data.reseller_id;
    _partnerEmail = email;

    sessionStorage.setItem(PORTAL_KEY, JSON.stringify({
      token: _token, resellerId: _resellerId, email: _partnerEmail,
    }));

    showApp();

  } catch (err) {
    errEl.textContent = 'Connection error. Is the server running at localhost:8000?';
    errEl.style.display = 'block';
  } finally {
    // Always restore button state
    btnText.style.display = 'block';
    spinner.style.display = 'none';
    document.getElementById('btn-login').disabled = false;
  }
}


// =============================================================================
// LOGOUT
// =============================================================================

/** Clear session state and return to the login screen. */
function doLogout() {
  sessionStorage.removeItem(PORTAL_KEY);
  _token = null;
  _resellerId = null;
  _partnerEmail = null;
  _partnerData = null;

  document.getElementById('app-screen').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';

  // Clear the login form fields
  const emailInput = document.getElementById('login-email');
  const secretInput = document.getElementById('login-secret');
  if (emailInput) emailInput.value = '';
  if (secretInput) secretInput.value = '';
}
