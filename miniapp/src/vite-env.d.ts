/// <reference types="vite/client" />

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        ready: () => void;
        expand: () => void;
        initData: string;
        initDataUnsafe?: { user?: { id?: number; username?: string } };
        themeParams: Record<string, string | undefined>;
        setHeaderColor: (color: string) => void;
        colorScheme?: string;
      };
    };
  }
}

export {};
