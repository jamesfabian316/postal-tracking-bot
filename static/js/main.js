// Constants
const API_ENDPOINTS = {
	TRACKING_NUMBERS: '/api/tracking/numbers',
	TRACKING_DATA: '/api/tracking',
	UPDATE_STATUS: '/api/tracking/update',
}

const STATUS_MAP = {
	Delivered: 'delivered',
	'In Transit': 'in-transit',
	Processing: 'processing',
	'Failed Delivery': 'failed',
}

// Utility Functions
const formatTrackingNumber = (number) => number.replace(/\s/g, '')

const showNotification = (message, type = 'success') => {
	const notification = document.createElement('div')
	notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg ${
		type === 'success' ? 'bg-green-500' : 'bg-red-500'
	} text-white z-50`
	notification.textContent = message
	document.body.appendChild(notification)

	setTimeout(() => {
		notification.remove()
	}, 3000)
}

// API Functions
const fetchData = async (url, options = {}) => {
	try {
		const response = await fetch(url, {
			...options,
			headers: {
				'Content-Type': 'application/json',
				...options.headers,
			},
		})

		if (!response.ok) {
			const error = await response.json()
			throw new Error(error.error || 'Failed to fetch data')
		}

		return await response.json()
	} catch (error) {
		console.error(`Error fetching ${url}:`, error)
		throw error
	}
}

// Fetch tracking numbers for dropdown
async function fetchTrackingNumbers() {
	try {
		const numbers = await fetchData(API_ENDPOINTS.TRACKING_NUMBERS)
		const select = document.getElementById('trackingNumber')

		select.innerHTML = '<option value="">Select a tracking number</option>'
		numbers.forEach((number) => {
			const displayNumber = formatTrackingNumber(number)
			select.innerHTML += `<option value="${displayNumber}">${displayNumber}</option>`
		})
	} catch (error) {
		showNotification('Failed to load tracking numbers', 'error')
	}
}

// Fetch and display tracking data
async function fetchTrackingData() {
	try {
		const data = await fetchData(API_ENDPOINTS.TRACKING_DATA)
		const tbody = document.getElementById('trackingList')

		// Use DocumentFragment for better performance
		const fragment = document.createDocumentFragment()

		data.forEach((item) => {
			const row = document.createElement('tr')
			const displayNumber = formatTrackingNumber(item.tracking_number)

			row.innerHTML = `
        <td class="font-mono">${displayNumber}</td>
        <td><span class="status-badge ${getStatusClass(item.status)}">${item.status}</span></td>
        <td>${item.status_details}</td>
        <td>${item.last_updated}</td>
      `

			fragment.appendChild(row)
		})

		tbody.innerHTML = ''
		tbody.appendChild(fragment)
	} catch (error) {
		showNotification('Failed to load tracking data', 'error')
	}
}

// Get status badge class
function getStatusClass(status) {
	return STATUS_MAP[status] || ''
}

// Event Handlers
function handleStatusChange(e) {
	const status = e.target.value
	const detailsInput = document.getElementById('statusDetails')

	if (status === 'Custom Status') {
		detailsInput.value = ''
	} else {
		detailsInput.value = statusOptions[status] || ''
	}
}

async function handleFormSubmit(e) {
	e.preventDefault()

	const form = e.target
	const formData = {
		tracking_number: form.trackingNumber.value,
		status: form.status.value,
		status_details: form.statusDetails.value,
	}

	try {
		await fetchData(API_ENDPOINTS.UPDATE_STATUS, {
			method: 'POST',
			body: JSON.stringify(formData),
		})

		showNotification('Status updated successfully')
		refreshData()
		form.reset()
	} catch (error) {
		showNotification('Failed to update status', 'error')
	}
}

// Initialize
function initialize() {
	// Get status options from hidden element
	const statusOptions = JSON.parse(document.getElementById('status-options').textContent)

	// Add event listeners
	document.getElementById('status').addEventListener('change', handleStatusChange)
	document.getElementById('updateForm').addEventListener('submit', handleFormSubmit)

	// Initial data load
	refreshData()
}

// Refresh all data
function refreshData() {
	fetchTrackingNumbers()
	fetchTrackingData()
}

// Start the application
document.addEventListener('DOMContentLoaded', initialize)
