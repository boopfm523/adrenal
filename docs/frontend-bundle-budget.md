# Frontend bundle budget

HealthCurve loads authentication, the private application shell, primary navigation, and the emergency-plan link in the initial JavaScript graph. Authenticated page modules are loaded as local route chunks when visited. No route depends on a third-party runtime asset, CDN, telemetry service, or model container.

Every production build enforces a maximum minified JavaScript chunk size of **450 KiB** using `frontend/scripts/check-bundle-size.mjs`. This is deliberately below Vite's 500 KiB warning threshold so growth fails the build before it silently recreates the warning. The budget measures emitted local `.js` files before compression because that is stable across developer and CI environments.

Run the same regression check with:

```bash
cd frontend
npm run build
```

Route loading announces `Loading page…` as a polite status. A failed route import renders a safe alert and reload action without showing raw exception details or health context. Direct navigation remains behind the normal authenticated route boundary.
