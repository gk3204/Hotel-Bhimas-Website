import React from "react";

const ImageWithSpinner = ({ src, alt = "", className = "", style = {} }) => {
  const [loaded, setLoaded] = React.useState(false);

  return (
    <div className="relative w-full h-full" style={style}>
      {/* Spinner shown while loading */}
      {!loaded && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-100 z-10">
          <div className="w-8 h-8 border-4 border-[#E5C07B] border-t-transparent rounded-full animate-spin"></div>
        </div>
      )}

      {/* Actual image */}
      <img
        src={src}
        alt={alt}
        onLoad={() => setLoaded(true)}
        className={`w-full h-full object-cover transition-opacity duration-500 ${
          loaded ? "opacity-100" : "opacity-0"
        } ${className}`}
      />
    </div>
  );
};

export default ImageWithSpinner;
