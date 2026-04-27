require("dotenv").config();
const express = require("express");
const mysql = require("mysql2");
const cors = require("cors");

const app = express();
const PORT = process.env.PORT || 5000;

// Middleware
app.use(cors());
app.use(express.json());

// MySQL Connection
const db = mysql.createConnection({
  host: process.env.DB_HOST, // "localhost"
  user: process.env.DB_USER, // "root"
  password: process.env.DB_PASS, // Your MySQL password
  database: process.env.DB_NAME, // "perfume_db"
});

db.connect((err) => {
  if (err) {
    console.error("MySQL Connection Error:", err);
  } else {
    console.log("MySQL Connected!");
  }
});

// Routes
app.get("/api/perfumes", (req, res) => {
  db.query("SELECT * FROM perfumes", (err, results) => {
    if (err) {
      res.status(500).json({ error: err.message });
    } else {
      res.json(results);
    }
  });
});

// Start server
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
