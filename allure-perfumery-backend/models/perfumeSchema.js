const mongoose = require("mongoose");

const allowedVolumes = {
  Decant: [5, 10, 20, 30],
  Tester: [30, 50, 100],
  Retail: [30, 50, 100]
};

const perfumeSchema = new mongoose.Schema({
  name: { type: String, required: true },
  brand: { type: String, required: true },
  price: { type: Number, required: true },
  image: { type: String, required: true },
  type: { 
    type: String, 
    enum: ["Partial", "Tester", "Decant", "Retail"], 
    required: true 
  },
  volume: { type: Number, required: true }, 
  stock: { type: Number, required: true, default: 1 }, // Track available units
  last_updated: { type: Date, default: Date.now }
});

// Validate volume before saving
perfumeSchema.pre("save", function (next) {
  if (this.type in allowedVolumes && !allowedVolumes[this.type].includes(this.volume)) {
    return next(new Error(`${this.type} can only have volumes: ${allowedVolumes[this.type].join(", ")}`));
  }
  next();
});

module.exports = mongoose.model("Perfume", perfumeSchema);
