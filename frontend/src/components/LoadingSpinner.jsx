import React from "react";

const LoadingSpinner = ({ message = "Loading..." }) => {
  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[9999]">
      <div className="bg-white rounded-2xl p-8 shadow-2xl text-center max-w-sm">
        {/* Spinner Animation */}
        <div className="flex justify-center mb-6">
          <div className="relative w-16 h-16">
            {/* Outer rotating ring */}
            <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-[#E5C07B] border-r-[#E5C07B] animate-spin"></div>
            {/* Inner rotating ring (opposite direction) */}
            <div className="absolute inset-2 rounded-full border-4 border-transparent border-b-[#D4AF37] border-l-[#D4AF37] animate-spin" style={{ animationDirection: "reverse" }}></div>
          </div>
        </div>

        {/* Message */}
        <p className="text-gray-700 font-semibold text-lg">{message}</p>
        
        {/* Dots animation */}
        <div className="flex justify-center gap-1 mt-4">
          <span className="w-2 h-2 bg-[#E5C07B] rounded-full animate-bounce" style={{ animationDelay: "0s" }}></span>
          <span className="w-2 h-2 bg-[#E5C07B] rounded-full animate-bounce" style={{ animationDelay: "0.2s" }}></span>
          <span className="w-2 h-2 bg-[#E5C07B] rounded-full animate-bounce" style={{ animationDelay: "0.4s" }}></span>
        </div>
      </div>
    </div>
  );
};

export default LoadingSpinner;
