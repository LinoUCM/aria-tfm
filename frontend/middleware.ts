import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const token = request.cookies.get('aria_token')?.value;
  const { pathname } = request.nextUrl;

  // 1. Si NO hay token y no está en /login, redirigir a /login
  if (!token && pathname !== '/login') {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  // 2. Si SÍ hay token y el usuario intenta entrar a /login, redirigir a /chat
  if (token && pathname === '/login') {
    return NextResponse.redirect(new URL('/chat', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
};