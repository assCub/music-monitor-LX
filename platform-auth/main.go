package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"

	"github.com/guohuiyuan/music-lib/bilibili"
	"github.com/guohuiyuan/music-lib/kugou"
	"github.com/guohuiyuan/music-lib/model"
	"github.com/guohuiyuan/music-lib/netease"
	"github.com/guohuiyuan/music-lib/qq"
	"github.com/guohuiyuan/music-lib/soda"
	qrcode "github.com/skip2/go-qrcode"
)

type createFn func() (*model.QRLoginSession, error)
type checkFn func(string) (*model.QRLoginResult, error)

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "sources": []string{"netease", "qq", "qq_wx", "kugou", "bilibili", "soda"}})
	})
	mux.HandleFunc("POST /api/qr/{source}", createQR)
	mux.HandleFunc("GET /api/qr/{source}", checkQR)
	mux.HandleFunc("POST /api/catalog/{source}/user-playlists", userPlaylists)
	mux.HandleFunc("POST /api/catalog/{source}/playlist/{id}", playlistSongs)
	port := os.Getenv("PORT")
	if port == "" {
		port = "8091"
	}
	log.Printf("platform-auth listening on %s", port)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}

type cookiePayload struct {
	Cookie string `json:"cookie"`
}

func readCookiePayload(w http.ResponseWriter, r *http.Request) (string, bool) {
	defer r.Body.Close()
	var payload cookiePayload
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20))
	if err := decoder.Decode(&payload); err != nil {
		writeJSON(w, 400, map[string]any{"error": "invalid cookie payload"})
		return "", false
	}
	if strings.TrimSpace(payload.Cookie) == "" {
		writeJSON(w, 400, map[string]any{"error": "cookie is required"})
		return "", false
	}
	return payload.Cookie, true
}

func userPlaylists(w http.ResponseWriter, r *http.Request) {
	cookie, ok := readCookiePayload(w, r)
	if !ok {
		return
	}
	var (
		items any
		err   error
	)
	switch r.PathValue("source") {
	case "kugou":
		items, err = kugou.New(cookie).GetUserPlaylists(1, 100)
	case "soda":
		items, err = soda.New(cookie).GetUserPlaylists(1, 100)
	default:
		writeJSON(w, 404, map[string]any{"error": "user playlists unsupported"})
		return
	}
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func playlistSongs(w http.ResponseWriter, r *http.Request) {
	cookie, ok := readCookiePayload(w, r)
	if !ok {
		return
	}
	playlistID := strings.TrimSpace(r.PathValue("id"))
	if playlistID == "" {
		writeJSON(w, 400, map[string]any{"error": "playlist id is required"})
		return
	}
	var (
		items any
		err   error
	)
	switch r.PathValue("source") {
	case "kugou":
		items, err = kugou.New(cookie).GetPlaylistSongs(playlistID)
	case "soda":
		items, err = soda.New(cookie).GetPlaylistSongs(playlistID)
	default:
		writeJSON(w, 404, map[string]any{"error": "playlist songs unsupported"})
		return
	}
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func provider(source string) (createFn, checkFn, bool) {
	switch strings.TrimSpace(source) {
	case "netease":
		return netease.CreateQRLogin, netease.CheckQRLogin, true
	case "qq":
		return qq.CreateQRLogin, qq.CheckQRLogin, true
	case "qq_wx":
		return qq.CreateWXQRLogin, qq.CheckWXQRLogin, true
	case "kugou":
		return kugou.CreateQRLogin, kugou.CheckQRLogin, true
	case "bilibili":
		return bilibili.CreateQRLogin, bilibili.CheckQRLogin, true
	case "soda":
		return soda.CreateQRLogin, soda.CheckQRLogin, true
	default:
		return nil, nil, false
	}
}

func createQR(w http.ResponseWriter, r *http.Request) {
	create, _, ok := provider(r.PathValue("source"))
	if !ok {
		writeJSON(w, 404, map[string]any{"error": "unsupported qr source"})
		return
	}
	session, err := create()
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": err.Error()})
		return
	}
	if session.ImageURL == "" && session.URL != "" {
		if png, qrErr := qrcode.Encode(session.URL, qrcode.Medium, 320); qrErr == nil {
			session.ImageURL = "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)
		}
	}
	writeJSON(w, 200, session)
}

func checkQR(w http.ResponseWriter, r *http.Request) {
	_, check, ok := provider(r.PathValue("source"))
	if !ok {
		writeJSON(w, 404, map[string]any{"error": "unsupported qr source"})
		return
	}
	key := strings.TrimSpace(r.URL.Query().Get("key"))
	if key == "" {
		writeJSON(w, 400, map[string]any{"error": "missing key"})
		return
	}
	result, err := check(key)
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": err.Error()})
		return
	}
	if result != nil && result.Status == model.QRLoginStatusSuccess && strings.TrimSpace(result.Cookie) == "" {
		result.Cookie = joinCookies(result.Cookies)
	}
	writeJSON(w, 200, result)
}

func joinCookies(cookies map[string]string) string {
	keys := make([]string, 0, len(cookies))
	for key := range cookies {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		if value := strings.TrimSpace(cookies[key]); value != "" {
			parts = append(parts, fmt.Sprintf("%s=%s", key, value))
		}
	}
	return strings.Join(parts, "; ")
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
