const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function loginUser(username: string, password: string) {
  const formData = new URLSearchParams();
  formData.append('username', username);
  formData.append('password', password);

  const response = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Credenciales inválidas');
  }

  const data = await response.json();
  
  // 1. Guardar en localStorage (para uso directo en componentes frontend)
  localStorage.setItem('aria_token', data.access_token);
  localStorage.setItem('aria_user', JSON.stringify(data.user));

  // 2. Guardar en Cookies (para que Next.js Middleware pueda leerlo en el servidor/Edge)
  // Duración: 24 horas (86400 segundos)
  document.cookie = `aria_token=${data.access_token}; path=/; max-age=86400; SameSite=Lax`;
  document.cookie = `aria_role=${data.user.role || 'operator'}; path=/; max-age=86400; SameSite=Lax`;

  return data;
}

export function getAuthToken(): string | null {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('aria_token');
  }
  return null;
}

export function getCurrentUser() {
  if (typeof window !== 'undefined') {
    const userStr = localStorage.getItem('aria_user');
    if (!userStr) return null;
    try {
      return JSON.parse(userStr);
    } catch {
      return null;
    }
  }
  return null;
}

export function logoutUser() {
  // Limpiar localStorage
  localStorage.removeItem('aria_token');
  localStorage.removeItem('aria_user');

  // Expirar Cookies inmediatamente
  document.cookie = 'aria_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';
  document.cookie = 'aria_role=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';

  // Redirigir al login
  window.location.href = '/login';
}