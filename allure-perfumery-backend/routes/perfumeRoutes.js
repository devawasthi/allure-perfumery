const express = require("express");
const Perfume = require("../models/perfumeSchema"); // Import Perfume model
const router = express.Router();
router.post("/", async (req, res) => {
    try {
      const { name, brand, price, image, type, volume, stock } = req.body;
  
      if (!allowedVolumes[type] || !allowedVolumes[type].includes(volume)) {
        return res.status(400).json({ 
          error: `${type} can only have volumes: ${allowedVolumes[type].join(", ")}` 
        });
      }
  
      const newPerfume = new Perfume({ name, brand, price, image, type, volume, stock });
      const savedPerfume = await newPerfume.save();
      
      res.status(201).json(savedPerfume);
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
    const express = require("express");
    const Perfume = require("../models/perfumeModel"); // Import your Perfume model
    const router = express.Router();
});

    // ✅ GET: Search, filter, sort & paginate perfumes
router.get("/", async (req, res) => {
    try {
      let { search, type, minPrice, maxPrice, volume, sortBy, order, page, limit } = req.query;
      let filters = { stock: { $gt: 0 } };
  
      // 🔍 Search by name or brand
      if (search) {
        filters.$or = [
          { name: { $regex: search, $options: "i" } },
          { brand: { $regex: search, $options: "i" } }
        ];
      }
  
      // 🎯 Filter by type, volume, and price
      if (type) filters.type = type;
      if (volume) filters.volume = parseInt(volume);
      if (minPrice && maxPrice) {
        filters.price = { $gte: parseInt(minPrice), $lte: parseInt(maxPrice) };
      } else if (minPrice) {
        filters.price = { $gte: parseInt(minPrice) };
      } else if (maxPrice) {
        filters.price = { $lte: parseInt(maxPrice) };
      }
  
      // 🔄 Sorting logic
      let sortOptions = {};
      if (sortBy) {
        let sortOrder = order === "desc" ? -1 : 1;
        sortOptions[sortBy] = sortOrder;
      } else {
        sortOptions["createdAt"] = -1; // Default: Newest first
      }
  
      // 📌 Pagination defaults
      let pageNumber = parseInt(page) || 1;
      let pageSize = parseInt(limit) || 10;
      let skip = (pageNumber - 1) * pageSize;
  
      // 🛍 Fetch perfumes with pagination
      const perfumes = await Perfume.find(filters)
        .sort(sortOptions)
        .skip(skip)
        .limit(pageSize);
  
      // 📊 Total count for frontend
      const totalPerfumes = await Perfume.countDocuments(filters);
  
      res.json({
        perfumes,
        totalPages: Math.ceil(totalPerfumes / pageSize),
        currentPage: pageNumber,
        totalPerfumes,
      });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
    module.exports = router;
    
  