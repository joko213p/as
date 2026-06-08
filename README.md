# 📸 Instagram Post Downloader – Bot Telegram

Bot Telegram qui télécharge tous les **posts** (photos + vidéos) d’un profil Instagram public.

- ✅ Posts (images + vidéos) uniquement
- ❌ Stories, Highlights et Reels exclus
- 📦 Vidéos regroupées dans un fichier ZIP
- 🗂️ Crée automatiquement un **topic** (forum group) nommé `@username`
- 🚫 Aucun cookie, aucun proxy, aucun compte Instagram requis

-----

## 🚀 Déploiement sur Railway

### 1. Créer le bot Telegram

1. Parle à [@BotFather](https://t.me/BotFather) sur Telegram
1. `/newbot` → donne un nom et un username
1. Copie le **token** fourni

### 2. Préparer le groupe Telegram

Pour utiliser les **topics**, ton groupe doit être un **“Supergroup Forum”** :

1. Crée un groupe Telegram
1. Paramètres → **Topics** → Activer
1. Ajoute ton bot comme **administrateur** avec les droits :
- Gérer les topics
- Envoyer des messages
- Envoyer des médias

### 3. Déployer sur Railway

1. Pousse ce dossier sur un repo GitHub
1. Va sur [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
1. Sélectionne ton repo

### 4. Variables d’environnement Railway

Dans Railway → ton service → **Variables**, ajoute :

|Variable   |Valeur                               |
|-----------|-------------------------------------|
|`BOT_TOKEN`|`123456:ABC-DEF...` (token BotFather)|

C’est tout ! Railway détecte automatiquement `requirements.txt` et `railway.toml`.

-----

## 💬 Utilisation

Envoie au bot (dans le groupe ou en direct) :

```
https://www.instagram.com/username
```

ou

```
@username
```

ou simplement

```
username
```

Le bot va :

1. Créer un topic `@username` dans le groupe
1. Télécharger tous les posts publics
1. Envoyer les photos en galerie
1. Envoyer les vidéos dans un fichier `username_videos.zip`

-----

## ⚠️ Limitations

- **Profils privés** : non supportés (pas de connexion Instagram)
- **Fichiers > 50 MB** : le ZIP est remplacé par un envoi vidéo par vidéo ; les fichiers > 50 MB individuels sont ignorés (limite Telegram Bot API)
- **Rate limiting Instagram** : pour les gros profils, instaloader peut ralentir automatiquement

-----

## 🛠️ Structure des fichiers

```
├── bot.py            # Code principal du bot
├── requirements.txt  # Dépendances Python
├── railway.toml      # Config Railway
├── Procfile          # Fallback process
└── README.md         # Ce fichier
```