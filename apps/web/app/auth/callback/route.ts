import { NextResponse } from 'next/server';
import { agentFetch, ID_TOKEN_COOKIE, PROFILE_COOKIE } from '@/lib/agent';

/**
 * Okta's redirect lands here with ?code and ?state.
 *
 * We hand both to the agent, which does the code exchange with private_key_jwt
 * and verifies the ID token. On success we set the same HttpOnly cookie the
 * rest of the storefront already reads. The browser never sees the id_token.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const err = url.searchParams.get('error');
  const errDesc = url.searchParams.get('error_description');

  if (err) {
    return NextResponse.redirect(
      new URL(
        `/?error=${encodeURIComponent(err)}&detail=${encodeURIComponent(errDesc ?? '')}`,
        url,
      ),
      302,
    );
  }
  if (!code || !state) {
    return NextResponse.redirect(new URL('/?error=missing_params', url), 302);
  }

  const upstream = await agentFetch('/auth/complete-signin', {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  });
  if (!upstream.ok) {
    const text = await upstream.text();
    return NextResponse.redirect(
      new URL(
        `/?error=signin_failed&detail=${encodeURIComponent(text.slice(0, 200))}`,
        url,
      ),
      302,
    );
  }

  const { id_token, profile, return_to } = (await upstream.json()) as {
    id_token: string;
    profile: { sub: string; email: string; name: string };
    return_to?: string;
  };

  const response = NextResponse.redirect(new URL(return_to ?? '/', url), 302);
  response.cookies.set(ID_TOKEN_COOKIE, id_token, {
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge: 3600,
    secure: process.env.NODE_ENV === 'production',
  });
  response.cookies.set(
    PROFILE_COOKIE,
    encodeURIComponent(JSON.stringify(profile)),
    {
      httpOnly: false,
      sameSite: 'lax',
      path: '/',
      maxAge: 3600,
    },
  );
  return response;
}
