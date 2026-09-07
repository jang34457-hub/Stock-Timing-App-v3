import { Platform } from 'react-native';

// 백엔드 API 주소. 필요 시 EXPO_PUBLIC_API_BASE 로 덮어쓴다.
// (Expo 는 EXPO_PUBLIC_ 접두 환경변수를 클라이언트에 노출한다.)
const DEFAULT_BASE =
  Platform.OS === 'web' ? 'http://localhost:8000' : 'http://10.0.2.2:8000';

export const API_BASE = process.env.EXPO_PUBLIC_API_BASE || DEFAULT_BASE;
