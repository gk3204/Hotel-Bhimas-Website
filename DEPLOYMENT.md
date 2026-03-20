# Hotel Bhimas - Deployment & Setup Guide

## 🚀 Complete Production Deployment Guide

### Overview
This guide covers deploying the Hotel Bhimas application (FastAPI backend + React frontend) for public access.

---

## 📋 Pre-Deployment Checklist

- [ ] Created `.env` file from `.env.example`
- [ ] Generated strong `SECRET_KEY` 
- [ ] Configured database credentials
- [ ] Set up Razorpay keys
- [ ] Configured email service
- [ ] Updated CORS origins
- [ ] Set admin restriction settings
- [ ] Tested locally with `docker-compose`

---

## 🏗️ Local Development Setup

### Backend Setup
```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your configuration

# Run backend
uvicorn main:app --reload
```

### Frontend Setup
```bash
cd frontend

# Install dependencies
npm install

# Create .env file
echo "VITE_API_URL=http://localhost:8000" > .env

# Run frontend
npm run dev
```

### With Docker Compose (Recommended)
```bash
# From project root
cp backend/.env.example backend/.env
# Edit backend/.env with your settings

docker-compose up --build
```

---

## 🌐 Production Deployment Options

### Option 1: Render.com (Free Tier)

**Pros:** Free tier available, easy deployment
**Cons:** 256MB database limit, auto-sleep after 15 min

#### Backend Deployment (Render)
1. Push code to GitHub
2. Go to [render.com](https://render.com)
3. Click "New" → "Web Service"
4. Connect GitHub repository
5. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port 8000`
6. Set environment variables from `.env`
7. Deploy

#### Database (Neon.tech - Free PostgreSQL)
1. Go to [neon.tech](https://neon.tech)
2. Create free PostgreSQL database
3. Copy connection string
4. Add to Render environment variables:
   ```
   DATABASE_URL=postgresql://user:password@host/dbname
   ```

#### Frontend Deployment (Vercel)
1. Go to [vercel.com](https://vercel.com)
2. Import GitHub repository (frontend folder)
3. Set environment variable:
   ```
   VITE_API_URL=https://your-render-backend.onrender.com
   ```
4. Deploy

---

### Option 2: DigitalOcean App Platform ($12/month)

#### Backend Setup on DigitalOcean
1. Create DigitalOcean account
2. Create App Platform project
3. Connect GitHub
4. Create services:
   - **API Service:** FastAPI backend
   - **Database Service:** PostgreSQL
   - **Web Server:** Nginx (optional)

#### Deployment Steps
```bash
# 1. Create app.yaml in root
# 2. Connect GitHub repository
# 3. DigitalOcean auto-detects and deploys
# 4. Set environment variables in dashboard
```

---

### Option 3: AWS EC2 (Cost Varies)

#### Setup EC2 Instance
```bash
# Connect to EC2 instance
ssh -i your-key.pem ec2-user@your-instance-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-pip postgresql postgresql-contrib nginx

# Clone repository
git clone https://github.com/yourusername/hotel-bhimas.git
cd hotel-bhimas

# Setup backend
cd backend
pip3 install -r requirements.txt
cp .env.example .env
# Edit .env with production values

# Configure Nginx
sudo tee /etc/nginx/sites-available/hotel-bhimas > /dev/null << EOF
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Enable site
sudo ln -s /etc/nginx/sites-available/hotel-bhimas /etc/nginx/sites-enabled/

# Test and reload Nginx
sudo nginx -t
sudo systemctl reload nginx

# Run backend with Gunicorn
pip3 install gunicorn
gunicorn -w 4 -b 127.0.0.1:8000 main:app
```

---

### Option 4: Cloudflare Tunnel (Free, Your PC as Server)

**Pros:** Completely free
**Cons:** Your computer must stay on 24/7

#### Setup
```bash
# 1. Install Cloudflare CLI
# Download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# 2. Run backend locally
uvicorn main:app --host 0.0.0.0 --port 8000

# 3. In another terminal, expose with tunnel
cloudflared tunnel --url http://localhost:8000

# Gets public URL: https://something.trycloudflare.com

# 4. Deploy frontend on Vercel
# Set VITE_API_URL=https://your-tunnel-url.trycloudflare.com
```

---

## 🔒 Security Checklist

### Required Before Going Public
- [ ] **Never commit `.env`** (use `.gitignore`)
- [ ] **Use strong SECRET_KEY** (min 32 characters, random)
- [ ] **Enable HTTPS** (Let's Encrypt free certificate)
- [ ] **Set RESTRICT_ADMIN_ACCESS=true** (production)
- [ ] **Configure CORS_ORIGINS** (only your frontend domain)
- [ ] **Use environment variables** for all secrets
- [ ] **Enable rate limiting** (already configured)
- [ ] **Add input validation** (already configured)
- [ ] **Enable database indexes** (already configured)
- [ ] **Regular backups** (database & files)

---

## 📊 Environment Variables

### Backend `.env` Template
```ini
# Database
DB_USER=postgres
DB_PASSWORD=your_secure_password
DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=hotelbhimas

# Security
SECRET_KEY=your_long_random_key_min_32_chars
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# CORS
CORS_ORIGINS=https://your-frontend.com,https://www.your-frontend.com

# Admin Security
RESTRICT_ADMIN_ACCESS=true
ALLOWED_ADMIN_NETWORKS=127.0.0.1,192.168.0.0/16

# Razorpay
RAZORPAY_KEY_ID=your_key_id
RAZORPAY_KEY_SECRET=your_key_secret

# Email
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_specific_password
MAIL_FROM=noreply@yourhotel.com
MAIL_PORT=587
MAIL_SERVER=smtp.gmail.com

# API Configuration
API_BASE_URL=https://api.yourhotel.com
FRONTEND_URL=https://yourhotel.com

# Logging
LOG_LEVEL=INFO

# Environment
ENVIRONMENT=production
```

### Frontend `.env` Template
```
VITE_API_URL=https://api.yourhotel.com
```

---

## 📈 Performance Optimization

### Database Optimization
```sql
-- Create indexes (already in models.py)
CREATE INDEX idx_booking_status ON bookings(status);
CREATE INDEX idx_booking_dates ON bookings(check_in, check_out);
CREATE INDEX idx_availability_room_date ON room_type_availability(room_type_id, date);
```

### Backend Optimization
- ✅ Rate limiting on sensitive endpoints
- ✅ Database connection pooling
- ✅ Pydantic validation
- ✅ Structured logging
- ✅ Error handling with try-catch

### Frontend Optimization
- Use production build: `npm run build`
- Enable gzip compression on server
- Use CDN for static assets
- Lazy load images

---

## 🐛 Troubleshooting

### Backend Won't Start
```bash
# Check database connection
python -c "from database import engine; print(engine)"

# Check dependencies
pip list | grep fastapi

# Check port 8000 is available
lsof -i :8000  # Linux/Mac
netstat -ano | findstr :8000  # Windows
```

### CORS Errors
- Verify `CORS_ORIGINS` in `.env`
- Check origin matches exactly (protocol + domain)
- Clear browser cache

### Razorpay Integration Issues
- Verify API keys are correct
- Test mode vs Live mode
- Check webhook endpoints

### Email Not Working
- Verify Gmail app password (not account password)
- Enable "Less secure app access" if needed
- Check MAIL_PORT=587 (not 465 or 25)

---

## 📚 Resources

- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [React Vite Deployment](https://vitejs.dev/)
- [PostgreSQL Hosting](https://www.postgresql.org/support/versioning/)
- [Docker Documentation](https://docs.docker.com/)
- [Razorpay Docs](https://razorpay.com/docs/)

---

## 🎯 Next Steps

1. **Test locally**
   ```bash
   docker-compose up
   ```

2. **Choose hosting** (Recommend DigitalOcean)

3. **Get domain name** (Namecheap, GoDaddy, etc.)

4. **Set up SSL/HTTPS** (Let's Encrypt free)

5. **Configure email** (Gmail/SendGrid)

6. **Monitor logs** and setup alerts

---

## 📞 Support

For issues or questions:
- Check logs: `docker-compose logs backend`
- Check GitHub issues
- Review error messages in browser console

---

**Last Updated:** March 20, 2026
