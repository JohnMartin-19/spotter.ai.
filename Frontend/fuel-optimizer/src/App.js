// src/App.js
import React, { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Polyline, Marker, Popup, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css'; // Don't forget to import Leaflet's CSS
import L from 'leaflet'; // Import Leaflet for custom icons and bounds
import './App.css'; // Your custom CSS for App.js

// --- Leaflet Icon Fix & Custom Icon ---
// This is necessary because Webpack/bundlers often change the paths Leaflet expects.
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.7.1/dist/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
});

// Custom icon for fuel stops (gas station icon!)
// You can use a local image, or a URL. For simplicity, let's use a common one.
// Make sure this URL is accessible or replace with a local path like '/images/gas-station-icon.png'
const gasStationIcon = new L.Icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png', // A green marker for gas
    // Or if you want a more specific gas pump icon, you'd need to find one:
    // iconUrl: 'https://cdn-icons-png.flaticon.com/512/114/114777.png', // Example: a generic gas pump icon (check license!)
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41]
});


// Helper component to update map view based on routeData
function MapUpdater({ routeData }) {
  const map = useMap(); // Access the map instance

  useEffect(() => {
    if (routeData && routeData.route_geometry && routeData.route_geometry.length > 0) {
      const validCoords = routeData.route_geometry.filter(coord => Array.isArray(coord) && coord.length === 2);
      if (validCoords.length > 0) {
        const bounds = new L.LatLngBounds(validCoords.map(coord => [coord[0], coord[1]]));
        map.fitBounds(bounds, { padding: [50, 50], animate: true, duration: 1 });
      } else {
        map.setView([39.8283, -98.5795], 4);
      }
    } else {
        map.setView([39.8283, -98.5795], 4);
    }
  }, [routeData, map]);

  return null;
}


function App() {
  const [startLocation, setStartLocation] = useState('');
  const [endLocation, setEndLocation] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [responseData, setResponseData] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResponseData(null);

    try {
      // Corrected to port 8000 based on typical Django setup
      const response = await fetch('http://localhost:8080/optimizer_app/api/v1/route-and-fuel/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          start_location: startLocation,
          end_location: endLocation,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        const errorMessage = errorData.error || errorData.detail || `Server responded with status ${response.status}`;
        throw new Error(errorMessage);
      }

      const data = await response.json();
      setResponseData(data);
      console.log("API Response:", data);
    } catch (err) {
      console.error("Error fetching route:", err);
      setError(err.message || 'Failed to fetch route. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Fuel Route Optimizer</h1>
      </header>

      <div className="form-section">
        <h2>Enter Route Details</h2>
        <form onSubmit={handleSubmit} className="route-form">
          <div className="form-group">
            <label htmlFor="startLocation">Start Location:</label>
            <input
              type="text"
              id="startLocation"
              value={startLocation}
              onChange={(e) => setStartLocation(e.target.value)}
              placeholder="e.g., Los Angeles, CA"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="endLocation">End Location:</label>
            <input
              type="text"
              id="endLocation"
              value={endLocation}
              onChange={(e) => setEndLocation(e.target.value)}
              placeholder="e.g., Dallas, TX"
              required
            />
          </div>
          <button type="submit" disabled={loading}>
            {loading ? 'Optimizing...' : 'Optimize Route'}
          </button>
        </form>
        {error && <p className="error-message">{error}</p>}
        {loading && <p className="loading-message">Calculating optimal route...</p>}
      </div>

      {responseData && (
        <div className="results-section">
          <h2>Optimization Results</h2>
          <p><strong>Total Distance:</strong> {responseData.total_distance_miles?.toFixed(2)} miles</p>
          <p><strong>Total Fuel Cost:</strong> ${responseData.total_fuel_cost_usd?.toFixed(2)} USD</p>
          {/* Use optional chaining for estimated_total_trip_duration_minutes as it might be missing */}
          <p>
            <strong>Estimated Total Trip Duration:</strong>
            {responseData.estimated_total_trip_duration_minutes ?
             ` ${responseData.estimated_total_trip_duration_minutes} minutes` : ' N/A'}
          </p>

          <h3>Optimal Fuel Stops:</h3>
          {responseData.optimal_fuel_stops && responseData.optimal_fuel_stops.length > 0 ? (
            <ul className="fuel-stops-list">
              {responseData.optimal_fuel_stops.map((stop, index) => (
                <li key={index} className="fuel-stop-item">
                  <strong>Stop {index + 1}:</strong> {stop.location}
                  {/* Access latitude and longitude directly from stop object */}
                  {stop.latitude && stop.longitude && (
                    <><br/>Lat: {stop.latitude.toFixed(4)}, Lon: {stop.longitude.toFixed(4)}</>
                  )}
                  {/* No 'distance_from_start_miles' in your console output, so it will remain blank unless backend sends it */}
                  <br/>Distance from start: {stop.distance_from_start_miles?.toFixed(2) || 'N/A'} miles
                  {/* Corrected key names: fuel_price_per_gallon, fuel_added_gallons */}
                  <br/>Fuel price: ${stop.fuel_price_per_gallon?.toFixed(2) || 'N/A'}/gallon, Gallons to fill: {stop.fuel_added_gallons?.toFixed(2) || 'N/A'}
                  {/* Corrected key name: cost_at_this_stop */}
                  <br/>Cost at stop: ${stop.cost_at_this_stop?.toFixed(2) || 'N/A'} USD
                  {/* Detour fields might also need checking if they are not consistently present */}
                  {stop.detour_distance_miles > 0 && (
                      <>
                          <br />Detour: {stop.detour_distance_miles?.toFixed(2)} miles, {Math.ceil(stop.detour_duration_seconds / 60)} minutes
                      </>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p>No optimal intermediate fuel stops required for this route (within vehicle range).</p>
          )}

          <div className="map-section">
            <h3>Route Map</h3>
            <div className="map-container">
              <MapContainer
                center={[39.8283, -98.5795]} // Default center (center of US)
                zoom={4} // Default zoom
                scrollWheelZoom={true}
                style={{ height: '100%', width: '100%' }}
              >
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />

                <MapUpdater routeData={responseData} />

                {/* Route Polyline - filtering out any non-array or improperly sized coordinate pairs */}
                {responseData.route_geometry && responseData.route_geometry.length > 0 && (
                  <Polyline
                    positions={responseData.route_geometry.filter(coord => Array.isArray(coord) && coord.length === 2).map(coord => [coord[0], coord[1]])}
                    color="blue"
                    weight={5}
                  />
                )}

                {/* Start Marker - ensuring it's an array of length 2 */}
                {responseData.start_coords && Array.isArray(responseData.start_coords) && responseData.start_coords.length === 2 && (
                  <Marker position={[responseData.start_coords[0], responseData.start_coords[1]]}>
                    <Popup>Start Location</Popup>
                  </Marker>
                )}

                {/* End Marker - ensuring it's an array of length 2 */}
                {responseData.end_coords && Array.isArray(responseData.end_coords) && responseData.end_coords.length === 2 && (
                  <Marker position={[responseData.end_coords[0], responseData.end_coords[1]]}>
                    <Popup>End Location</Popup>
                  </Marker>
                )}

                {/* Optimal Fuel Stops Markers - Using latitude/longitude directly and the new gasStationIcon */}
                {responseData.optimal_fuel_stops && responseData.optimal_fuel_stops.map((stop, index) => (
                  // Check if both latitude and longitude exist for the stop
                  stop.latitude != null && stop.longitude != null && ( // Explicit check for null/undefined
                    <Marker
                      key={`fuel-stop-${index}`}
                      position={[stop.latitude, stop.longitude]} // Use latitude and longitude directly
                      icon={gasStationIcon} // Use the custom gas station icon
                    >
                      <Popup>
                        <h3>Fuel Stop {index + 1}</h3>
                        <p>Location: {stop.location}</p>
                        <p>Price: ${stop.fuel_price_per_gallon?.toFixed(2) || 'N/A'}/gallon</p>
                        <p>Fill: {stop.fuel_added_gallons?.toFixed(2) || 'N/A'} gallons</p>
                        <p>Cost: ${stop.cost_at_this_stop?.toFixed(2) || 'N/A'}</p>
                        {/* If distance_from_start_miles is still N/A, consider if backend should send it */}
                        <p>Distance from start: {stop.distance_from_start_miles?.toFixed(2) || 'N/A'} miles</p>
                        {stop.detour_distance_miles > 0 && (
                          <p>Detour: {stop.detour_distance_miles?.toFixed(2)} miles ({Math.ceil(stop.detour_duration_seconds / 60)} mins)</p>
                        )}
                      </Popup>
                    </Marker>
                  )
                ))}
              </MapContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;